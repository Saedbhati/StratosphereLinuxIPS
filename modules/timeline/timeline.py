import traceback
import sys
import time
import json
from typing import Any

from slips_files.common.parsers.config_parser import ConfigParser
from slips_files.common.slips_utils import utils
from slips_files.common.abstracts.module import IModule


class Timeline(IModule):
    # Name: short name of the module. Do not use spaces
    name = "Timeline"
    description = (
        "Creates kalipso timeline of what happened in the"
        " network based on flows and available data"
    )
    authors = ["Sebastian Garcia"]

    def init(self):
        self.separator = self.db.get_field_separator()
        # Subscribe to 'new_flow' channel
        self.c1 = self.db.subscribe("new_flow")
        self.channels = {
            "new_flow": self.c1,
        }
        # Read information how we should print timestamp.
        conf = ConfigParser()
        self.is_human_timestamp = conf.timeline_human_timestamp()
        self.analysis_direction = conf.analysis_direction()

    def convert_timestamp_to_slips_format(self, timestamp: float) -> str:
        if self.is_human_timestamp:
            timestamp = utils.convert_format(timestamp, utils.alerts_format)
        return str(timestamp)

    def validate_bytes(self, bytes: Any) -> int:
        if not isinstance(bytes, int):
            bytes = 0
        return bytes

    def is_inbound_traffic(self, profileid, daddr) -> bool:
        """return True if profileid's IP is the same as the daddr"""
        profile_ip = profileid.split("_")[1]
        return self.analysis_direction == "all" and str(daddr) == str(
            profile_ip
        )

    def process_dns_altflow(self, alt_flow: dict):
        answer = alt_flow["answers"]
        if "NXDOMAIN" in alt_flow["rcode_name"]:
            answer = "NXDOMAIN"
        dns_activity = {
            "query": alt_flow["query"],
            "answers": answer,
        }
        alt_activity = {
            "info": dns_activity,
            "critical warning": "",
        }
        return alt_activity

    def process_http_altflow(self, alt_flow: dict):
        http_data_all = {
            "Request": alt_flow["method"]
            + " http://"
            + alt_flow["host"]
            + alt_flow["uri"],
            "Status Code": str(alt_flow["status_code"])
            + "/"
            + alt_flow["status_msg"],
            "MIME": str(alt_flow["resp_mime_types"]),
            "UA": alt_flow["user_agent"],
        }
        # if any of fields are empty, do not include them
        http_data = {
            k: v for k, v in http_data_all.items() if v != "" and v != "/"
        }
        return {"info": http_data}

    def process_ssl_altflow(self, alt_flow: dict):
        if alt_flow["validation_status"] == "ok":
            validation = "Yes"
            resumed = "False"
        elif not alt_flow["validation_status"] and alt_flow["resumed"] is True:
            # If there is no validation and it is a resumed ssl.
            # It means that there was a previous connection with
            # the validation data. We can not say Say it
            validation = "??"
            resumed = "True"
        else:
            # If the validation is not ok and not empty
            validation = "No"
            resumed = "False"
        # if there is no CN
        subject = (
            alt_flow["subject"].split(",")[0]
            if alt_flow["subject"]
            else "????"
        )
        # We put server_name instead of dns resolution
        ssl_activity = {
            "server_name": subject,
            "trusted": validation,
            "resumed": resumed,
            "version": alt_flow["version"],
            "dns_resolution": alt_flow["server_name"],
        }
        return {"info": ssl_activity}

    def process_ssh_altflow(self, alt_flow: dict):
        success = (
            "Successful" if alt_flow["auth_success"] else "Not Successful"
        )
        ssh_activity = {
            "login": success,
            "auth_attempts": alt_flow["auth_attempts"],
            "client": alt_flow["client"],
            "server": alt_flow["client"],
        }
        return {"info": ssh_activity}

    def process_altflow(self, profileid, twid, uid) -> dict:
        alt_flow: dict = self.db.get_altflow_from_uid(profileid, twid, uid)
        altflow_info = {"info": ""}

        if not alt_flow:
            return altflow_info

        flow_type = alt_flow["type_"]
        flow_type_map = {
            "dns": self.process_dns_altflow,
            "http": self.process_http_altflow,
            "ssl": self.process_ssl_altflow,
            "ssh": self.process_ssh_altflow,
        }
        try:
            altflow_info = flow_type_map[flow_type](alt_flow)
        except KeyError:
            pass
        return altflow_info

    def get_dns_resolution(self, ip):
        """
        returns a list or a str with the dns resolution of the given ip
        """
        dns_resolution: dict = self.db.get_dns_resolution(ip)
        dns_resolution: list = dns_resolution.get("domains", [])

        # we should take only one resolution, if there is more
        # than 3, because otherwise it does not fit in the timeline.
        if len(dns_resolution) > 3:
            dns_resolution = dns_resolution[-1]

        if not dns_resolution:
            dns_resolution = "????"
        return dns_resolution

    def process_tcp_udp_flow(
        self, profileid: str, dport_name: str, flow: dict
    ):
        dur = round(float(flow["dur"]), 3)
        daddr = flow["daddr"]
        state = flow["state"]
        stime = flow["ts"]
        dport = flow["dport"]
        proto = flow["proto"].upper()
        sbytes = self.validate_bytes(flow["sbytes"])
        allbytes = self.validate_bytes(flow["allbytes"])
        timestamp_human = self.convert_timestamp_to_slips_format(stime)

        critical_warning_dport_name = ""
        if not dport_name:
            dport_name = "????"
            critical_warning_dport_name = (
                "Protocol not recognized by Slips nor Zeek."
            )

        activity = {
            "timestamp": timestamp_human,
            "dport_name": dport_name,
            "preposition": (
                "from" if self.is_inbound_traffic(profileid, daddr) else "to"
            ),
            "dns_resolution": self.get_dns_resolution(daddr),
            "daddr": daddr,
            "dport/proto": f"{str(dport)}/{proto}",
            "state": state,
            "warning": "No data exchange!" if not allbytes else "",
            "info": "",
            "sent": sbytes,
            "recv": allbytes - sbytes,
            "tot": allbytes,
            "duration": dur,
            "critical warning": critical_warning_dport_name,
        }
        return activity

    def process_icmp_flow(self, profileid: str, dport_name: str, flow: dict):
        sport = flow["sport"]
        dport = flow["dport"]
        stime = flow["ts"]
        saddr = flow["saddr"]
        daddr = flow["daddr"]
        dur = round(float(flow["dur"]), 3)
        allbytes = self.validate_bytes(flow["allbytes"])
        timestamp_human = self.convert_timestamp_to_slips_format(stime)

        extra_info = {}
        warning = ""

        # Zeek format
        if isinstance(sport, int):
            icmp_types = {
                11: "ICMP Time Exceeded in Transit",
                3: "ICMP Destination Net Unreachable",
                8: "PING echo",
            }
            try:
                dport_name = icmp_types[sport]
            except KeyError:
                dport_name = "ICMP Unknown type"
                extra_info["type"] = f"0x{str(sport)}"

        # Argus format
        elif isinstance(sport, str):
            icmp_types_str = {
                "0x0008": "PING echo",
                "0x0103": "ICMP Host Unreachable",
                "0x0303": "ICMP Port Unreachable",
                "0x000b": "",
                "0x0003": "ICMP Destination Net Unreachable",
            }

            dport_name = icmp_types_str.get(sport, "ICMP Unknown type")

            if sport == "0x0303":
                warning = f"Unreachable port is {int(dport, 16)}"

        activity = {
            "timestamp": timestamp_human,
            "dport_name": dport_name,
            "preposition": "from",
            "saddr": saddr,
            "size": allbytes,
            "duration": dur,
        }

        extra_info.update(
            {
                "dns_resolution": "",
                "daddr": daddr,
                "dport/proto": f"{sport}/ICMP",
                "state": "",
                "warning": warning,
                "sent": "",
                "recv": "",
                "tot": "",
                "critical warning": "",
            }
        )
        activity.update(extra_info)
        return activity

    def process_igmp_flow(self, profileid: str, dport_name: str, flow: dict):
        stime = flow["ts"]
        dur = round(float(flow["dur"]), 3)
        saddr = flow["daddr"]
        allbytes = self.validate_bytes(flow["allbytes"])
        timestamp_human = self.convert_timestamp_to_slips_format(stime)
        return {
            "timestamp": timestamp_human,
            "dport_name": "IGMP",
            "preposition": "from",
            "saddr": saddr,
            "size": allbytes,
            "duration": dur,
        }

    def interpret_dport(self, flow) -> str:
        """tries to get a meaningful name of the dport used
        in the given flow"""
        dport_name = flow.get("appproto", "")
        # suricata does this
        if not dport_name or dport_name == "failed":
            dport = flow["dport"]
            proto = flow["proto"]
            dport_name = self.db.get_port_info(f"{dport}/{proto.lower()}")
        dport_name = "" if not dport_name else dport_name.upper()
        return dport_name

    def process_flow(self, profileid, twid, flow, timestamp: float):
        """
        Process the received flow  for this profileid and twid
         so its printed by the logprocess later
        """
        try:
            uid = next(iter(flow))
            flow: dict = json.loads(flow[uid])
            proto = flow["proto"].upper()
            dport_name = self.interpret_dport(flow)
            # interpret the given flow and and create an activity line to
            # display in slips Web interface/Kalipso
            # Change the format of timeline in the case of inbound
            # flows for external IP, i.e direction 'all' and destination IP
            # == profile IP.
            # If not changed, it would have printed  'IP1 https asked to IP1'.
            proto_handlers = {
                "TCP": self.process_tcp_udp_flow,
                "UDP": self.process_tcp_udp_flow,
                "ICMP": self.process_icmp_flow,
                "IPV6-ICMP": self.process_icmp_flow,
                "IPV4-ICMP": self.process_icmp_flow,
                "IGMP": self.process_igmp_flow,
            }
            if proto in proto_handlers:
                activity = proto_handlers[proto](profileid, dport_name, flow)
            else:
                activity = {}
            #################################
            # Now process the alternative flows
            # Sometimes we need to wait a little to give time to Zeek to find
            # the related flow since they are read very fast together.
            # This should be improved algorithmically probably
            time.sleep(0.05)
            alt_activity = self.process_altflow(profileid, twid, uid)
            # Combine the activity of normal flows and activity of alternative
            # flows and store in the DB for this profileid and twid
            activity.update(alt_activity)
            self.db.add_timeline_line(profileid, twid, activity, timestamp)

        except Exception:
            exception_line = sys.exc_info()[2].tb_lineno
            self.print(
                f"Problem on process_flow() line {exception_line}", 0, 1
            )
            self.print(traceback.format_exc(), 0, 1)
            return True

    def pre_main(self):
        utils.drop_root_privs()

    def main(self):
        # Main loop function
        if msg := self.get_msg("new_flow"):
            mdata = msg["data"]
            mdata = json.loads(mdata)
            profileid = mdata["profileid"]
            twid = mdata["twid"]
            flow = mdata["flow"]
            timestamp = mdata["stime"]
            flow = json.loads(flow)
            self.process_flow(profileid, twid, flow, timestamp)
