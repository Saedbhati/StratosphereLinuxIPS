import contextlib
import ipaddress
import json
import sys
from datetime import datetime
from typing import Tuple, List, Dict
import validators

from modules.flowalerts.dns import DNS
from modules.flowalerts.timer_thread import TimerThread
from slips_files.common.abstracts.flowalerts_analyzer import (
    IFlowalertsAnalyzer,
)
from slips_files.common.parsers.config_parser import ConfigParser
from slips_files.common.slips_utils import utils


class Conn(IFlowalertsAnalyzer):
    def init(self):
        # get the default gateway
        self.gateway = self.db.get_gateway_ip()
        self.p2p_daddrs = {}
        # If 1 flow uploaded this amount of MBs or more,
        # slips will alert data upload
        self.flow_upload_threshold = 100
        self.read_configuration()
        # Cache list of connections that we already checked in the timer
        # thread (we waited for the dns resolution for these connections)
        self.connections_checked_in_conn_dns_timer_thread = []
        self.whitelist = self.flowalerts.whitelist
        # Threshold how much time to wait when capturing in an interface,
        # to start reporting connections without DNS
        # Usually the computer resolved DNS already, so we need to wait a little to report
        # In mins
        self.conn_without_dns_interface_wait_time = 30
        self.dns_analyzer = DNS(self.db, flowalerts=self)

    def read_configuration(self):
        conf = ConfigParser()
        self.long_connection_threshold = conf.long_connection_threshold()
        self.data_exfiltration_threshold = conf.data_exfiltration_threshold()
        self.data_exfiltration_threshold = conf.data_exfiltration_threshold()
        self.our_ips = utils.get_own_ips()
        self.client_ips: List[str] = conf.client_ips()

    def name(self) -> str:
        return "conn_analyzer"

    def check_long_connection(
        self, dur, daddr, saddr, profileid, twid, uid, timestamp
    ):
        """
        Check if a duration of the connection is
        above the threshold (more than 25 minutes by default).
        :param dur: duration of the flow in seconds
        """

        if (
            ipaddress.ip_address(daddr).is_multicast
            or ipaddress.ip_address(saddr).is_multicast
        ):
            # Do not check the duration of the flow
            return

        if isinstance(dur, str):
            dur = float(dur)

        # If duration is above threshold, we should set an evidence
        if dur > self.long_connection_threshold:
            self.set_evidence.long_connection(
                daddr, dur, profileid, twid, uid, timestamp
            )
            return True
        return False

    def is_p2p(self, dport, proto, daddr):
        """
        P2P is defined as following : proto is udp, port numbers are higher than 30000 at least 5 connections to different daddrs
        OR trying to connct to 1 ip on more than 5 unkown 30000+/udp ports
        """
        if proto.lower() == "udp" and int(dport) > 30000:
            try:
                # trying to connct to 1 ip on more than 5 unknown ports
                if self.p2p_daddrs[daddr] >= 6:
                    return True
                self.p2p_daddrs[daddr] = self.p2p_daddrs[daddr] + 1
                # now check if we have more than 4 different dst ips
            except KeyError:
                # first time seeing this daddr
                self.p2p_daddrs[daddr] = 1

            if len(self.p2p_daddrs) >= 5:
                # this is another connection on port 3000+/udp and we already have 5 of them
                # probably p2p
                return True

        return False

    def port_belongs_to_an_org(self, daddr, portproto, profileid):
        """
        Checks wehether a port is known to be used by a specific
        organization or not, and returns true if the daddr belongs to the
        same org as the port
        """
        organization_info = self.db.get_organization_of_port(portproto)
        if not organization_info:
            # consider this port as unknown, it doesn't belong to any org
            return False

        # there's an organization that's known to use this port,
        # check if the daddr belongs to the range of this org
        organization_info = json.loads(organization_info)

        # get the organization ip or range
        org_ips: list = organization_info["ip"]

        # org_name = organization_info['org_name']

        if daddr in org_ips:
            # it's an ip and it belongs to this org, consider the port as known
            return True

        for ip in org_ips:
            # is any of them a range?
            with contextlib.suppress(ValueError):
                # we have the org range in our database, check if the daddr belongs to this range
                if ipaddress.ip_address(daddr) in ipaddress.ip_network(ip):
                    # it does, consider the port as known
                    return True

        # not a range either since nothing is specified, e.g. ip is set to ""
        # check the source and dst mac address vendors
        src_mac_vendor = str(self.db.get_mac_vendor_from_profile(profileid))
        dst_mac_vendor = str(
            self.db.get_mac_vendor_from_profile(f"profile_{daddr}")
        )

        # get the list of all orgs known to use this port and proto
        for org_name in organization_info["org_name"]:
            org_name = org_name.lower()
            if (
                org_name in src_mac_vendor.lower()
                or org_name in dst_mac_vendor.lower()
            ):
                return True

            # check if the SNI, hostname, rDNS of this ip belong to org_name
            ip_identification = self.db.get_ip_identification(daddr)
            if org_name in ip_identification.lower():
                return True

            # if it's an org that slips has info about (apple, fb, google,etc.),
            # check if the daddr belongs to it
            if bool(self.whitelist.org_analyzer.is_ip_in_org(daddr, org_name)):
                return True

        return False

    def check_unknown_port(
        self, dport, proto, daddr, profileid, twid, uid, timestamp, state
    ):
        """
        Checks dports that are not in our
        slips_files/ports_info/services.csv
        """
        if not dport:
            return
        if state != "Established":
            # detect unknown ports on established conns only
            return False

        portproto = f"{dport}/{proto}"
        if self.db.get_port_info(portproto):
            # it's a known port
            return False

        # we don't have port info in our database
        # is it a port that is known to be used by
        # a specific organization?
        if self.port_belongs_to_an_org(daddr, portproto, profileid):
            return False

        if (
            "icmp" not in proto
            and not self.is_p2p(dport, proto, daddr)
            and not self.db.is_ftp_port(dport)
        ):
            # we don't have info about this port
            self.set_evidence.unknown_port(
                daddr, dport, proto, timestamp, profileid, twid, uid
            )
            return True

    def check_multiple_reconnection_attempts(
        self, origstate, saddr, daddr, dport, uid, profileid, twid, timestamp
    ):
        """
        Alerts when 5+ reconnection attempts from the same source IP to
        the same destination IP occurs
        """
        if origstate != "REJ":
            return

        key = f"{saddr}-{daddr}-{dport}"

        # add this conn to the stored number of reconnections
        current_reconnections = self.db.get_reconnections_for_tw(
            profileid, twid
        )

        try:
            reconnections, uids = current_reconnections[key]
            reconnections += 1
            uids.append(uid)
            current_reconnections[key] = (reconnections, uids)
        except KeyError:
            current_reconnections[key] = (1, [uid])
            reconnections = 1

        if reconnections < 5:
            return

        self.set_evidence.multiple_reconnection_attempts(
            profileid,
            twid,
            daddr,
            uids,
            timestamp,
            reconnections,
        )
        # reset the reconnection attempts of this src->dst
        current_reconnections[key] = (0, [])

        self.db.setReconnections(profileid, twid, current_reconnections)

    def is_ignored_ip_data_upload(self, ip):
        """
        Ignore the IPs that we shouldn't alert about
        """

        ip_obj = ipaddress.ip_address(ip)
        if (
            ip == self.gateway
            or ip_obj.is_multicast
            or ip_obj.is_link_local
            or ip_obj.is_reserved
        ):
            return True

        return False

    def get_sent_bytes(
        self, all_flows: Dict[str, dict]
    ) -> Dict[str, Tuple[int, List[str], str]]:
        """
        Returns a dict of sent bytes to all ips in the all_flows dict
         {
            contacted_ip: (
                sum_of_mbs_sent,
                [uids],
                last_ts_of_flow_containging_this_contacted_ip
            )
        }
        """
        bytes_sent = {}
        for uid, flow in all_flows.items():
            daddr = flow["daddr"]
            sbytes: int = int(flow.get("sbytes", 0))
            ts: str = flow.get("starttime", "")

            if self.is_ignored_ip_data_upload(daddr) or not sbytes:
                continue

            if daddr in bytes_sent:
                mbs_sent, uids, _ = bytes_sent[daddr]
                mbs_sent += int(sbytes)
                uids.append(uid)
                bytes_sent[daddr] = (mbs_sent, uids, ts)
            else:
                bytes_sent[daddr] = (sbytes, [uid], ts)

        return bytes_sent

    def detect_data_upload_in_twid(self, profileid, twid):
        """
        For each contacted ip in this twid,
        check if the total bytes sent to this ip is >= data_exfiltration_threshold
        """
        all_flows: Dict[str, dict] = self.db.get_all_flows_in_profileid(
            profileid
        )
        if not all_flows:
            return

        bytes_sent: Dict[str, Tuple[int, List[str], str]]
        bytes_sent = self.get_sent_bytes(all_flows)

        for ip, ip_info in bytes_sent.items():
            ip_info: Tuple[int, List[str], str]
            bytes_uploaded, uids, ts = ip_info
            mbs_uploaded = utils.convert_to_mb(bytes_uploaded)
            if mbs_uploaded < self.data_exfiltration_threshold:
                continue

            self.set_evidence.data_exfiltration(
                ip, mbs_uploaded, profileid, twid, uids, ts
            )

    def check_device_changing_ips(
        self, flow_type, smac, profileid, twid, uid, timestamp
    ):
        """
        Every time we have a flow for a new ip
            (an ip that we're seeing for the first time)
        we check if the MAC of this srcip was associated with another ip
        this check is only done once for each source ip slips sees
        """
        if "conn" not in flow_type:
            return

        if not smac:
            return

        saddr: str = profileid.split("_")[-1]
        if not (
            validators.ipv4(saddr)
            and utils.is_private_ip(ipaddress.ip_address(saddr))
        ):
            return

        if self.db.was_ip_seen_in_connlog_before(saddr):
            # we should only check once for the first
            # time we're seeing this flow
            return
        self.db.mark_srcip_as_seen_in_connlog(saddr)

        if old_ip_list := self.db.get_ip_of_mac(smac):
            # old_ip is a list that may contain the ipv6 of this MAC
            # this ipv6 may be of the same device that
            # has the given saddr and MAC
            # so this would be fp. so, make sure we're dealing with ipv4 only
            for ip in json.loads(old_ip_list):
                if validators.ipv4(ip):
                    old_ip = ip
                    break
            else:
                # all the IPs associated with the given macs are ipv6,
                # 1 computer might have several ipv6,
                # AND/OR a combination of ipv6 and 4
                # so this detection will only work if both the
                # old ip and the given saddr are ipv4 private ips
                return

            if old_ip != saddr:
                # we found this smac associated with an
                # ip other than this saddr
                self.set_evidence.device_changing_ips(
                    smac, old_ip, profileid, twid, uid, timestamp
                )

    def check_data_upload(
        self, sbytes, daddr, uid: str, profileid, twid, timestamp
    ):
        """
        Set evidence when 1 flow is sending >= the flow_upload_threshold bytes
        """
        if not daddr or self.is_ignored_ip_data_upload(daddr) or not sbytes:
            return False

        src_mbs = utils.convert_to_mb(int(sbytes))
        if src_mbs >= self.flow_upload_threshold:
            self.set_evidence.data_exfiltration(
                daddr,
                src_mbs,
                profileid,
                twid,
                [uid],
                timestamp,
            )
            return True
        return False

    def should_ignore_conn_without_dns(
        self, flow_type, appproto, daddr
    ) -> bool:
        """
        checks for the cases that we should ignore the connection without dns
        """
        # we should ignore this evidence if the ip is ours, whether it's a
        # private ip or in the list of client_ips
        return (
            flow_type != "conn"
            or appproto in ("dns", "icmp")
            or utils.is_ignored_ip(daddr)
            # if the daddr is a client ip, it means that this is a conn
            # from the internet to our ip, the dns res was probably
            # made on their side before connecting to us,
            # so we shouldn't be doing this detection on this ip
            or daddr in self.client_ips
            # because there's no dns.log to know if the dns was made
            or self.db.get_input_type() == "zeek_log_file"
            or self.db.is_doh_server(daddr)
            or self.dns_analyzer.is_dns_server(daddr)
        )

    def check_if_resolution_was_made_by_different_version(
        self, profileid, daddr
    ):
        """
        Sometimes the same computer makes dns requests using its ipv4 and ipv6 address, check if this is the case
        """
        # get the other ip version of this computer
        other_ip = self.db.get_the_other_ip_version(profileid)
        if other_ip:
            other_ip = json.loads(other_ip)
        # get the domain of this ip
        dns_resolution = self.db.get_dns_resolution(daddr)

        try:
            if other_ip and other_ip in dns_resolution.get("resolved-by", []):
                return True
        except AttributeError:
            # It can be that the dns_resolution sometimes gives back a list and gets this error
            pass
        return False

    def check_connection_without_dns_resolution(
        self, flow_type, appproto, daddr, twid, profileid, timestamp, uid
    ):
        """
        Checks if there's a flow to a dstip that has no cached DNS answer
        """
        # The exceptions are:
        # 1- Do not check for DNS requests
        # 2- Ignore some IPs like private IPs, multicast, and broadcast

        if self.should_ignore_conn_without_dns(flow_type, appproto, daddr):
            return

        # Ignore some IP
        ## - All dhcp servers. Since is ok to connect to
        # them without a DNS request.
        # We dont have yet the dhcp in the redis, when is there check it
        # if self.db.get_dhcp_servers(daddr):
        # continue

        # To avoid false positives in case of an interface
        # don't alert ConnectionWithoutDNS
        # until 30 minutes has passed
        # after starting slips because the dns may have happened before starting slips
        if "-i" in sys.argv or self.db.is_growing_zeek_dir():
            # connection without dns in case of an interface,
            # should only be detected from the srcip of this device,
            # not all ips, to avoid so many alerts of this type when port scanning
            saddr = profileid.split("_")[-1]
            if saddr not in self.our_ips:
                return False

            start_time = self.db.get_slips_start_time()
            now = datetime.now()
            diff = utils.get_time_diff(start_time, now, return_type="minutes")
            if diff < self.conn_without_dns_interface_wait_time:
                # less than 30 minutes have passed
                return False

        # search 24hs back for a dns resolution
        if self.db.is_ip_resolved(daddr, 24):
            return False

        if uid not in self.connections_checked_in_conn_dns_timer_thread:
            # comes here if we haven't started the timer
            # thread for this connection before
            # mark this connection as checked
            self.connections_checked_in_conn_dns_timer_thread.append(uid)
            params = [
                flow_type,
                appproto,
                daddr,
                twid,
                profileid,
                timestamp,
                uid,
            ]

            # There is no DNS resolution, but it can be that Slips is
            # still reading it from the files.
            # To give time to Slips to read all the files and get all the flows
            # don't alert a Connection Without DNS until 5 seconds has passed
            # in real time from the time of this checking.
            timer = TimerThread(
                15, self.check_connection_without_dns_resolution, params
            )
            timer.start()
        else:
            # It means we already checked this conn with the Timer process
            # (we waited 15 seconds for the dns to arrive after
            # the connection was made)
            # but still no dns resolution for it.
            # Sometimes the same computer makes requests using
            # its ipv4 and ipv6 address, check if this is the case
            if self.check_if_resolution_was_made_by_different_version(
                profileid, daddr
            ):
                return False

            if self.is_well_known_org(daddr):
                # if the SNI or rDNS of the IP matches a
                # well-known org, then this is a FP
                return False

            self.set_evidence.conn_without_dns(
                daddr, timestamp, profileid, twid, uid
            )
            # This UID will never appear again, so we can remove it and
            # free some memory
            with contextlib.suppress(ValueError):
                self.connections_checked_in_conn_dns_timer_thread.remove(uid)

    def check_conn_to_port_0(
        self,
        sport,
        dport,
        proto,
        saddr,
        daddr,
        profileid,
        twid,
        uid,
        timestamp,
    ):
        """
        Alerts on connections to or from port 0 using protocols other than
        igmp, icmp
        """
        if proto.lower() in ("igmp", "icmp", "ipv6-icmp", "arp"):
            return

        if sport != 0 and dport != 0:
            return

        attacker = saddr if sport == 0 else daddr
        victim = saddr if attacker == daddr else daddr
        self.set_evidence.for_port_0_connection(
            saddr,
            daddr,
            sport,
            dport,
            profileid,
            twid,
            uid,
            timestamp,
            victim,
            attacker,
        )

    def detect_connection_to_multiple_ports(
        self,
        saddr,
        daddr,
        proto,
        state,
        appproto,
        dport,
        timestamp,
        profileid,
        twid,
    ):
        if proto != "tcp" or state != "Established":
            return

        dport_name = appproto
        if not dport_name:
            dport_name = self.db.get_port_info(f"{dport}/{proto}")

        if dport_name:
            # dport is known, we are considering only unknown services
            return

        # Connection to multiple ports to the destination IP
        if profileid.split("_")[1] == saddr:
            direction = "Dst"
            state = "Established"
            protocol = "TCP"
            role = "Client"
            type_data = "IPs"

            # get all the dst ips with established tcp connections
            daddrs = self.db.get_data_from_profile_tw(
                profileid,
                twid,
                direction,
                state,
                protocol,
                role,
                type_data,
            )

            # make sure we find established connections to this daddr
            if daddr not in daddrs:
                return

            dstports = list(daddrs[daddr]["dstports"])
            if len(dstports) <= 1:
                return

            uids = daddrs[daddr]["uid"]

            victim: str = daddr
            attacker: str = profileid.split("_")[-1]

            self.set_evidence.connection_to_multiple_ports(
                profileid,
                twid,
                uids,
                timestamp,
                dstports,
                victim,
                attacker,
            )

        # Connection to multiple port to the Source IP.
        # Happens in the mode 'all'
        elif profileid.split("_")[-1] == daddr:
            direction = "Src"
            state = "Established"
            protocol = "TCP"
            role = "Server"
            type_data = "IPs"

            # get all the src ips with established tcp connections
            saddrs = self.db.get_data_from_profile_tw(
                profileid,
                twid,
                direction,
                state,
                protocol,
                role,
                type_data,
            )
            dstports = list(saddrs[saddr]["dstports"])
            if len(dstports) <= 1:
                return

            uids = saddrs[saddr]["uid"]
            attacker: str = daddr
            victim: str = profileid.split("_")[-1]

            self.set_evidence.connection_to_multiple_ports(
                profileid, twid, uids, timestamp, dstports, victim, attacker
            )

    def check_non_http_port_80_conns(
        self,
        state,
        daddr,
        dport,
        proto,
        appproto,
        allbytes,
        profileid,
        twid,
        uid,
        timestamp,
    ):
        """
        alerts on established connections on port 80 that are not HTTP
        """
        # if it was a valid http conn, the 'service' field aka
        # appproto should be 'http'
        if (
            str(dport) == "80"
            and proto.lower() == "tcp"
            and appproto.lower() != "http"
            and state == "Established"
            and allbytes != 0
        ):
            self.set_evidence.non_http_port_80_conn(
                daddr, profileid, timestamp, twid, uid
            )

    def is_well_known_org(self, ip):
        """get the SNI, ASN, and  rDNS of the IP to check if it belongs
        to a well-known org"""

        ip_data = self.db.get_ip_info(ip)
        try:
            sni = ip_data["SNI"]
            if isinstance(sni, list):
                # SNI is a list of dicts, each dict contains the
                # 'server_name' and 'port'
                sni = sni[0]
                if sni in (None, ""):
                    sni = False
                elif isinstance(sni, dict):
                    sni = sni.get("server_name", False)
        except (KeyError, TypeError):
            # No SNI data for this ip
            sni = False

        try:
            rdns = ip_data["reverse_dns"]
        except (KeyError, TypeError):
            # No SNI data for this ip
            rdns = False

        flow_domains = [rdns, sni]
        for org in utils.supported_orgs:
            for domain in flow_domains:
                if self.whitelist.org_analyzer.is_ip_asn_in_org_asn(ip, org):
                    return True

                # we have the rdns or sni of this flow , now check
                if domain and self.whitelist.org_analyzer.is_domain_in_org(
                    domain, org
                ):
                    return True

                # check if the ip belongs to the range of a well known org
                # (fb, twitter, microsoft, etc.)
                if self.whitelist.org_analyzer.is_ip_in_org(ip, org):
                    return True
            return False

    def check_different_localnet_usage(
        self,
        saddr,
        daddr,
        dport,
        proto,
        profileid,
        timestamp,
        twid,
        uid,
        what_to_check="",
    ):
        """
        alerts when a connection to a private ip that
        doesn't belong to our local network is found
        for example:
        If we are on 192.168.1.0/24 then detect anything
        coming from/to 10.0.0.0/8
        :param what_to_check: can be 'srcip' or 'dstip'
        """
        ip_to_check = saddr if what_to_check == "srcip" else daddr
        ip_obj = ipaddress.ip_address(ip_to_check)
        own_local_network = self.db.get_local_network()

        if not own_local_network:
            # the current local network wasn't set in the db yet
            # it's impossible to get here becaus ethe localnet is set before
            # any msg is published in the new_flow channel
            return

        if not (validators.ipv4(ip_to_check) and utils.is_private_ip(ip_obj)):
            return

        # if it's a private ipv4 addr, it should belong to our local network
        if ip_obj in ipaddress.IPv4Network(own_local_network):
            return

        self.set_evidence.different_localnet_usage(
            daddr,
            f"{dport}/{proto}",
            profileid,
            timestamp,
            twid,
            uid,
            ip_outside_localnet=what_to_check,
        )

    def check_connection_to_local_ip(
        self,
        daddr,
        dport,
        proto,
        saddr,
        twid,
        uid,
        timestamp,
    ):
        """
        Alerts when there's a connection from a private IP to
        another private IP except for DNS connections to the gateway
        """

        def is_dns_conn():
            return (
                dport == 53
                and proto.lower() == "udp"
                and daddr == self.db.get_gateway_ip()
            )

        with contextlib.suppress(ValueError):
            dport = int(dport)

        if is_dns_conn():
            # skip DNS conns to the gw to avoid having tons of this evidence
            return

        # make sure the 2 ips are private
        if not (
            utils.is_private_ip(ipaddress.ip_address(saddr))
            and utils.is_private_ip(ipaddress.ip_address(daddr))
        ):
            return

        self.set_evidence.conn_to_private_ip(
            proto,
            daddr,
            dport,
            saddr,
            twid,
            uid,
            timestamp,
        )

    def analyze(self, msg):
        if utils.is_msg_intended_for(msg, "new_flow"):
            new_flow = json.loads(msg["data"])
            profileid = new_flow["profileid"]
            twid = new_flow["twid"]
            flow = new_flow["flow"]
            flow = json.loads(flow)
            uid = next(iter(flow))
            flow_dict = json.loads(flow[uid])
            # Flow type is 'conn' or 'dns', etc.
            flow_type = flow_dict["flow_type"]
            dur = flow_dict["dur"]
            saddr = flow_dict["saddr"]
            daddr = flow_dict["daddr"]
            origstate = flow_dict["origstate"]
            state = flow_dict["state"]
            timestamp = new_flow["stime"]
            sport: int = flow_dict["sport"]
            dport: int = flow_dict.get("dport", None)
            proto = flow_dict.get("proto")
            sbytes = flow_dict.get("sbytes", 0)
            appproto = flow_dict.get("appproto", "")
            smac = flow_dict.get("smac", "")
            allbytes = flow_dict.get("allbytes", 0)
            if not appproto or appproto == "-":
                appproto = flow_dict.get("type", "")

            self.check_long_connection(
                dur, daddr, saddr, profileid, twid, uid, timestamp
            )
            self.check_unknown_port(
                dport,
                proto.lower(),
                daddr,
                profileid,
                twid,
                uid,
                timestamp,
                state,
            )
            self.check_multiple_reconnection_attempts(
                origstate, saddr, daddr, dport, uid, profileid, twid, timestamp
            )
            self.check_conn_to_port_0(
                sport,
                dport,
                proto,
                saddr,
                daddr,
                profileid,
                twid,
                uid,
                timestamp,
            )
            self.check_different_localnet_usage(
                saddr,
                daddr,
                dport,
                proto,
                profileid,
                timestamp,
                twid,
                uid,
                what_to_check="srcip",
            )
            self.check_different_localnet_usage(
                saddr,
                daddr,
                dport,
                proto,
                profileid,
                timestamp,
                twid,
                uid,
                what_to_check="dstip",
            )

            self.check_connection_without_dns_resolution(
                flow_type, appproto, daddr, twid, profileid, timestamp, uid
            )

            self.detect_connection_to_multiple_ports(
                saddr,
                daddr,
                proto,
                state,
                appproto,
                dport,
                timestamp,
                profileid,
                twid,
            )
            self.check_data_upload(
                sbytes, daddr, uid, profileid, twid, timestamp
            )

            self.check_non_http_port_80_conns(
                state,
                daddr,
                dport,
                proto,
                appproto,
                allbytes,
                profileid,
                twid,
                uid,
                timestamp,
            )

            self.check_connection_to_local_ip(
                daddr,
                dport,
                proto,
                saddr,
                twid,
                uid,
                timestamp,
            )

            self.check_device_changing_ips(
                flow_type, smac, profileid, twid, uid, timestamp
            )

        if utils.is_msg_intended_for(msg, "tw_closed"):
            profileid_tw = msg["data"].split("_")
            profileid = f"{profileid_tw[0]}_{profileid_tw[1]}"
            twid = profileid_tw[-1]
            self.detect_data_upload_in_twid(profileid, twid)
