#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#
#
# This file is built up from an autogenerated template resource_server.py and
# contains code/hooks at different point during processing a request, specific
# to type of resource. For eg. allocation of mac/ip-addr for a port during its
# creation.

import json
import re

import cfgm_common
from vnc_quota import *
from gen.resource_xsd import *
from gen.resource_common import *
from gen.resource_server import *
from pprint import pformat
import uuid

class GlobalSystemConfigServer(GlobalSystemConfigServerGen):
    @classmethod
    def _check_asn(cls, obj_dict, db_conn):
        global_asn = obj_dict.get('autonomous_system')
        if not global_asn:
            return (True, '')
        (ok, result) = db_conn.dbe_list('virtual-network')
        if not ok:
            return (ok, result)
        for vn_name, vn_uuid in result:
            ok, result = db_conn.dbe_read('virtual-network', {'uuid': vn_uuid})
            if not ok:
                return ok, result
            rt_dict = result.get('route_target_list', {})
            for rt in rt_dict.get('route_target', []):
                (_, asn, target) = rt.split(':')
                if (int(asn) == global_asn and
                    int(target) >= cfgm_common.BGP_RTGT_MIN_ID):
                    return (False, (400, "Virtual network %s is configured "
                            "with a route target with this ASN and route "
                            "target value in the same range as used by "
                            "automatically allocated route targets" % vn_name))
        return (True, '')
    # end _check_asn

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        ok, result = cls._check_asn(obj_dict, db_conn)
        if not ok:
            return ok, result
        return True, ''
    # end http_post_collection

    @classmethod
    def http_put(cls, id, fq_name, obj_dict, db_conn):
        ok, result = cls._check_asn(obj_dict, db_conn)
        if not ok:
            return ok, result
        return True, ''
    # end http_put

# end class GlobalSystemConfigServer


class FloatingIpServer(FloatingIpServerGen):
    generate_default_instance = False

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        proj_dict = obj_dict['project_refs'][0]
        if 'uuid' in proj_dict:
            proj_uuid = proj_dict['uuid']
        else:
            proj_uuid = db_conn.fq_name_to_uuid('project', proj_dict['to'])
        (ok, proj_dict) = QuotaHelper.get_project_dict(proj_uuid, db_conn)
        if not ok:
            return (False, (500, 'Internal error : ' + pformat(proj_dict)))

        obj_type = 'floating-ip'
        QuotaHelper.ensure_quota_project_present(obj_type, proj_uuid, proj_dict, db_conn)

        if 'floating_ip_back_refs' in proj_dict:
            quota_count = len(proj_dict['floating_ip_back_refs'])
            (ok, quota_limit) = QuotaHelper.check_quota_limit(proj_dict, obj_type, quota_count)
            if not ok:
                return (False, (403, pformat(obj_dict['fq_name']) + ' : ' + quota_limit))

        vn_fq_name = obj_dict['fq_name'][:-2]
        req_ip = obj_dict.get("floating_ip_address", None)
        try:
            fip_addr = cls.addr_mgmt.ip_alloc_req(
                vn_fq_name, asked_ip_addr=req_ip)
        except Exception as e:
            return (False, (500, str(e)))
        obj_dict['floating_ip_address'] = fip_addr
        print 'AddrMgmt: alloc %s FIP for vn=%s, tenant=%s, askip=%s' \
            % (obj_dict['floating_ip_address'], vn_fq_name, tenant_name,
               req_ip)
        return True, ""
    # end http_post_collection

    @classmethod
    def http_post_collection_fail(cls, tenant_name, obj_dict, db_conn):
        vn_fq_name = obj_dict['fq_name'][:-2]
        fip_addr = obj_dict['floating_ip_address']
        print 'AddrMgmt: free FIP %s for vn=%s tenant=%s, on post fail' % (fip_addr, vn_fq_name, tenant_name)
        cls.addr_mgmt.ip_free_req(fip_addr, vn_fq_name)
        return True, ""
    # end http_post_collection_fail

    @classmethod
    def http_delete(cls, id, obj_dict, db_conn):
        vn_fq_name = obj_dict['fq_name'][:-2]
        fip_addr = obj_dict['floating_ip_address']
        print 'AddrMgmt: free FIP %s for vn=%s' % (fip_addr, vn_fq_name)
        cls.addr_mgmt.ip_free_req(fip_addr, vn_fq_name)
        return True, ""
    # end http_delete

    @classmethod
    def http_delete_fail(cls, id, obj_dict, db_conn):
        vn_fq_name = obj_dict['fq_name'][:-2]
        req_ip = obj_dict.get("floating_ip_address", None)
        if req_ip == None:
            return True, ""
        try:
            cls.addr_mgmt.ip_alloc_req(vn_fq_name, asked_ip_addr=req_ip)
        except Exception as e:
            return (False, (500, str(e)))
        print 'AddrMgmt: alloc %s FIP for vn=%s, tenant=%s to recover DELETE failure' \
            % (obj_dict['floating_ip_address'], vn_fq_name, tenant_name)
        return True, ""
    # end http_delete_fail

    @classmethod
    def dbe_create_notification(cls, obj_ids, obj_dict):
        fip_addr = obj_dict['floating_ip_address']
        vn_fq_name = obj_dict['fq_name'][:-2]
        cls.addr_mgmt.ip_alloc_notify(fip_addr, vn_fq_name)
    # end dbe_create_notification

    @classmethod
    def dbe_delete_notification(cls, obj_ids, obj_dict):
        fip_addr = obj_dict['floating_ip_address']
        vn_fq_name = obj_dict['fq_name'][:-2]
        cls.addr_mgmt.ip_free_notify(fip_addr, vn_fq_name)
    # end dbe_delete_notification

# end class FloatingIpServer


class InstanceIpServer(InstanceIpServerGen):
    generate_default_instance = False

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        vn_fq_name = obj_dict['virtual_network_refs'][0]['to']
        if ((vn_fq_name == cfgm_common.IP_FABRIC_VN_FQ_NAME) or
                (vn_fq_name == cfgm_common.LINK_LOCAL_VN_FQ_NAME)):
            # Ignore ip-fabric and link-local address allocations
            return True,  ""

        req_ip = obj_dict.get("instance_ip_address", None)
        try:
            ip_addr = cls.addr_mgmt.ip_alloc_req(
                vn_fq_name, asked_ip_addr=req_ip)
        except Exception as e:
            return (False, (500, str(e)))
        obj_dict['instance_ip_address'] = ip_addr
        print 'AddrMgmt: alloc %s for vn=%s, tenant=%s, askip=%s' \
            % (obj_dict['instance_ip_address'],
               vn_fq_name, tenant_name, req_ip)
        return True, ""
    # end http_post_collection

    @classmethod
    def http_post_collection_fail(cls, tenant_name, obj_dict, db_conn):
        vn_fq_name = obj_dict['virtual_network_refs'][0]['to']
        if ((vn_fq_name == cfgm_common.IP_FABRIC_VN_FQ_NAME) or
                (vn_fq_name == cfgm_common.LINK_LOCAL_VN_FQ_NAME)):
            # Ignore ip-fabric and link-local address allocations
            return True,  ""

        ip_addr = obj_dict['instance_ip_address']
        print 'AddrMgmt: free IP %s, vn=%s tenant=%s on post fail' % (ip_addr, vn_fq_name, tenant_name)
        cls.addr_mgmt.ip_free_req(ip_addr, vn_fq_name)
        return True, ""
    # end http_post_collection_fail

    @classmethod
    def http_delete(cls, id, obj_dict, db_conn):
        vn_fq_name = obj_dict['virtual_network_refs'][0]['to']
        if ((vn_fq_name == cfgm_common.IP_FABRIC_VN_FQ_NAME) or
                (vn_fq_name == cfgm_common.LINK_LOCAL_VN_FQ_NAME)):
            # Ignore ip-fabric and link-local address allocations
            return True,  ""

        ip_addr = obj_dict['instance_ip_address']
        print 'AddrMgmt: free IP %s, vn=%s' % (ip_addr, vn_fq_name)
        cls.addr_mgmt.ip_free_req(ip_addr, vn_fq_name)
        return True, ""
    # end http_delete

    @classmethod
    def http_delete_fail(cls, id, obj_dict, db_conn):
        vn_fq_name = obj_dict['virtual_network_refs'][0]['to']
        if ((vn_fq_name == cfgm_common.IP_FABRIC_VN_FQ_NAME) or
                (vn_fq_name == cfgm_common.LINK_LOCAL_VN_FQ_NAME)):
            # Ignore ip-fabric and link-local address allocations
            return True,  ""

        req_ip = obj_dict.get("instance_ip_address", None)
        if req_ip == None:
            return True, ""
        try:
            cls.addr_mgmt.ip_alloc_req(vn_fq_name, asked_ip_addr=req_ip)
        except Exception as e:
            return (False, (500, str(e)))
        print 'AddrMgmt: alloc %s for vn=%s, tenant=%s to recover DELETE failure' \
            % (obj_dict['instance_ip_address'], vn_fq_name, tenant_name)
        return True, ""
    # end http_delete_fail

    @classmethod
    def dbe_create_notification(cls, obj_ids, obj_dict):
        ip_addr = obj_dict['instance_ip_address']
        vn_fq_name = obj_dict['virtual_network_refs'][0]['to']
        cls.addr_mgmt.ip_alloc_notify(ip_addr, vn_fq_name)
    # end dbe_create_notification

    @classmethod
    def dbe_delete_notification(cls, obj_ids, obj_dict):
        ip_addr = obj_dict['instance_ip_address']
        vn_fq_name = obj_dict['virtual_network_refs'][0]['to']
        cls.addr_mgmt.ip_free_notify(ip_addr, vn_fq_name)
    # end dbe_delete_notification

# end class InstanceIpServer


class LogicalRouterServer(LogicalRouterServerGen):
    generate_default_instance = False

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        try:
            fq_name = obj_dict['fq_name']
            proj_uuid = db_conn.fq_name_to_uuid('project', fq_name[0:2])
        except NoIdError:
            return (False, (500, 'No Project ID error : ' + proj_uuid))

        (ok, proj_dict) = QuotaHelper.get_project_dict(proj_uuid, db_conn)
        if not ok:
            return (False, (500, 'Internal error : ' + pformat(proj_dict)))

        obj_type = 'logical-router'
        QuotaHelper.ensure_quota_project_present(obj_type, proj_uuid, proj_dict, db_conn)
        if 'logical_routers' in proj_dict:
            quota_count = len(proj_dict['logical_routers'])
            (ok, quota_limit) = QuotaHelper.check_quota_limit(proj_dict, obj_type, quota_count)
            if not ok:
                return (False, (403, pformat(obj_dict['fq_name']) + ' : ' + quota_limit))
        return True, ""
    # end http_post_collection

# end class LogicalRouterServer


class VirtualMachineInterfaceServer(VirtualMachineInterfaceServerGen):
    generate_default_instance = False

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        vn_dict = obj_dict['virtual_network_refs'][0]
        vn_uuid = vn_dict['uuid']
        (ok, vn_dict) = QuotaHelper.get_objtype_dict(vn_uuid, 'virtual-network', db_conn)
        if not ok:
            return (False, (500, 'Internal error : ' + pformat(vn_dict)))

        if vn_dict['parent_type'] == 'project':
            proj_uuid = vn_dict['parent_uuid']
            (ok, proj_dict) = QuotaHelper.get_project_dict(proj_uuid, db_conn)
            if not ok:
                return (False, (500, 'Internal error : ' + pformat(proj_dict)))

            obj_type = 'virtual-machine-interface'
            QuotaHelper.ensure_quota_project_present(obj_type, proj_uuid, proj_dict, db_conn)
            if 'virtual_machine_interfaces' in proj_dict:
                quota_count = len(proj_dict['virtual_machine_interfaces'])
                (ok, quota_limit) = QuotaHelper.check_quota_limit(proj_dict, obj_type, quota_count)
                if not ok:
                    return (False, (403, pformat(obj_dict['fq_name']) + ' : ' + quota_limit))

        inmac = None
        if 'virtual_machine_interface_mac_addresses' in obj_dict:
            mc = obj_dict['virtual_machine_interface_mac_addresses']
            if 'mac_address' in mc:
                if len(mc['mac_address'])==1:
                    inmac = mc['mac_address']
        if inmac!=None:
            mac_addrs_obj = MacAddressesType(inmac)
        else:
            mac_addr = cls.addr_mgmt.mac_alloc(obj_dict)
            mac_addrs_obj = MacAddressesType([mac_addr])
        mac_addrs_json = json.dumps(
            mac_addrs_obj,
            default=lambda o: dict((k, v)
                                   for k, v in o.__dict__.iteritems()))
        mac_addrs_dict = json.loads(mac_addrs_json)
        obj_dict['virtual_machine_interface_mac_addresses'] = mac_addrs_dict
        return True, ""
    # end http_post_collection

# end class VirtualMachineInterfaceServer


class VirtualNetworkServer(VirtualNetworkServerGen):

    @classmethod
    def _check_route_targets(cls, obj_dict, db_conn):
        if 'route_target_list' not in obj_dict:
            return (True, '')
        config_uuid = db_conn.fq_name_to_uuid('global_system_config', ['default-global-system-config'])
        config = db_conn.uuid_to_obj_dict(config_uuid)
        global_asn = config.get('prop:autonomous_system')
        if not global_asn:
            return (True, '')
        global_asn = json.loads(global_asn)
        rt_dict = obj_dict.get('route_target_list')
        if not rt_dict:
            return (True, '')
        for rt in rt_dict.get('route_target', []):
            (_, asn, target) = rt.split(':')
            if asn == global_asn and int(target) >= cfgm_common.BGP_RTGT_MIN_ID:
                 return (False, "Configured route target must use ASN that is "
                         "different from global ASN or route target value must"
                         " be less than %d" % cfgm_common.BGP_RTGT_MIN_ID)
        return (True, '')
    # end _check_route_targets

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        try:
            fq_name = obj_dict['fq_name']
            proj_uuid = db_conn.fq_name_to_uuid('project', fq_name[0:2])
        except NoIdError:
            return (False, (500, 'No Project ID error : ' + proj_uuid))

        (ok, proj_dict) = QuotaHelper.get_project_dict(proj_uuid, db_conn)
        if not ok:
            return (False, (500, 'Internal error : ' + pformat(proj_dict)))

        obj_type = 'virtual-network'
        QuotaHelper.ensure_quota_project_present(obj_type, proj_uuid, proj_dict, db_conn)
        if 'virtual_networks' in proj_dict:
            quota_count = len(proj_dict['virtual_networks'])
            (ok, quota_limit) = QuotaHelper.check_quota_limit(proj_dict, obj_type, quota_count)
            if not ok:
                return (False, (403, pformat(obj_dict['fq_name']) + ' : ' + quota_limit))

        (ok, error) =  cls._check_route_targets(obj_dict, db_conn)
        if not ok:
            return (False, (400, error))
        try:
            cls.addr_mgmt.net_create_req(obj_dict)
        except Exception as e:
            return (False, (500, str(e)))

        return True, ""
    # end http_post_collection

    @classmethod
    def http_post_collection_fail(cls, tenant_name, obj_dict, db_conn):
        cls.addr_mgmt.net_delete_req(obj_dict)
        return True, ""
    # end post_collection_fail

    @classmethod
    def http_put(cls, id, fq_name, obj_dict, db_conn):
        if ((fq_name == cfgm_common.IP_FABRIC_VN_FQ_NAME) or
                (fq_name == cfgm_common.LINK_LOCAL_VN_FQ_NAME)):
            # Ignore ip-fabric subnet updates
            return True,  ""

        if 'network_ipam_refs' not in obj_dict:
            # NOP for addr-mgmt module
            return True,  ""

        vn_id = {'uuid': id}
        (read_ok, read_result) = db_conn.dbe_read('virtual-network', vn_id)
        if not read_ok:
            return (False, (500, read_result))

        (ok, result) = cls.addr_mgmt.net_check_subnet_quota(read_result,
                                                            obj_dict, db_conn)
        if not ok:
            return (ok, (403, result))
        (ok, result) = cls.addr_mgmt.net_check_subnet_overlap(read_result,
                                                              obj_dict)
        if not ok:
            return (ok, (409, result))
        (ok, result) = cls.addr_mgmt.net_check_subnet_delete(read_result,
                                                             obj_dict)
        if not ok:
            return (ok, (409, result))

        try:
            cls.addr_mgmt.net_update_req(fq_name, read_result, obj_dict, id)
        except Exception as e:
            return (False, (500, str(e)))

        (ok, error) =  cls._check_route_targets(obj_dict, db_conn)
        if not ok:
            return (False, (400, error))
        return True, ""
    # end http_put

    @classmethod
    def http_put_fail(cls, id, fq_name, obj_dict, db_conn):
        if ((fq_name == cfgm_common.IP_FABRIC_VN_FQ_NAME) or
                (fq_name == cfgm_common.LINK_LOCAL_VN_FQ_NAME)):
            # Ignore ip-fabric subnet updates
            return True,  ""

        ipam_refs = obj_dict.get('network_ipam_refs', None)
        if not ipam_refs:
            # NOP for addr-mgmt module
            return True,  ""

        vn_id = {'uuid': id}
        (read_ok, read_result) = db_conn.dbe_read('virtual-network', vn_id)
        if not read_ok:
            return (False, (500, read_result))
        cls.addr_mgmt.net_update_req(fq_name, obj_dict, read_result, id)
    # end http_put_fail

    @classmethod
    def http_delete(cls, id, obj_dict, db_conn):
        cls.addr_mgmt.net_delete_req(obj_dict)
        return True, ""
    # end http_delete

    @classmethod
    def http_delete_fail(cls, id, obj_dict, db_conn):
        cls.addr_mgmt.net_create_req(obj_dict)
        return True, ""
    # end http_delete_fail

    @classmethod
    def ip_alloc(cls, vn_fq_name, subnet_name, count):
        ip_list = [cls.addr_mgmt.ip_alloc_req(vn_fq_name, subnet_name)
                   for i in range(count)]
        print 'AddrMgmt: reserve %d IP for vn=%s, subnet=%s - %s' \
            % (count, vn_fq_name, subnet_name if subnet_name else '', ip_list)
        return {'ip_addr': ip_list}
    # end ip_alloc

    @classmethod
    def ip_free(cls, vn_fq_name, subnet_name, ip_list):
        print 'AddrMgmt: release IP %s for vn=%s, subnet=%s' \
            % (ip_list, vn_fq_name, subnet_name if subnet_name else '')
        for ip_addr in ip_list:
            cls.addr_mgmt.ip_free_req(ip_addr, vn_fq_name, subnet_name)
    # end ip_free

    @classmethod
    def subnet_ip_count(cls, obj_dict, subnet_list):
        ip_count_list = []
        for item in subnet_list:
            ip_count_list.append(cls.addr_mgmt.ip_count(obj_dict, item))
        return {'ip_count_list': ip_count_list}
    # end subnet_ip_count

    @classmethod
    def dbe_create_notification(cls, obj_ids, obj_dict):
        cls.addr_mgmt.net_create_notify(obj_ids, obj_dict)
    # end dbe_create_notification

    @classmethod
    def dbe_update_notification(cls, obj_ids):
        cls.addr_mgmt.net_update_notify(obj_ids)
    # end dbe_update_notification

    @classmethod
    def dbe_delete_notification(cls, obj_ids, obj_dict):
        cls.addr_mgmt.net_delete_notify(obj_ids, obj_dict)
    # end dbe_update_notification

# end class VirtualNetworkServer


class NetworkIpamServer(NetworkIpamServerGen):

    @classmethod
    def http_put(cls, id, fq_name, obj_dict, db_conn):
        ipam_uuid = obj_dict['uuid']
        ipam_id = {'uuid': ipam_uuid}
        (read_ok, read_result) = db_conn.dbe_read('network-ipam', ipam_id)
        if not read_ok:
            return (False, (500, "Internal error : IPAM is not valid"))
        old_ipam_mgmt = read_result.get('network_ipam_mgmt')
        new_ipam_mgmt = obj_dict.get('network_ipam_mgmt')
        if not old_ipam_mgmt or not new_ipam_mgmt:
            return True, ""
        old_dns_method = old_ipam_mgmt.get('ipam_dns_method')
        new_dns_method = new_ipam_mgmt.get('ipam_dns_method')
        if not cls.is_change_allowed(old_dns_method, new_dns_method, obj_dict,
                                     db_conn):
            return (False, (409, "Cannot change DNS Method from " +
                    old_dns_method + " to " + new_dns_method +
                    " with active VMs referring to the IPAM"))
        return True, ""
    # end http_put

    @classmethod
    def http_put_fail(cls, id, fq_name, obj_dict, db_conn):
        # undo any state change done by http_put function
        return True, ""
    # end http_put_fail

    @classmethod
    def is_change_allowed(cls, old, new, obj_dict, db_conn):
        if (old == "default-dns-server" or old == "virtual-dns-server"):
            if ((new == "tenant-dns-server" or new == "none") and
                    cls.is_active_vm_present(obj_dict, db_conn)):
                return False
        if (old == "tenant-dns-server" and new != old and
                cls.is_active_vm_present(obj_dict, db_conn)):
            return False
        if (old == "none" and new != old and
                cls.is_active_vm_present(obj_dict, db_conn)):
            return False
        return True
    # end is_change_allowed

    @classmethod
    def is_active_vm_present(cls, obj_dict, db_conn):
        if 'virtual_network_back_refs' in obj_dict:
            vn_backrefs = obj_dict['virtual_network_back_refs']
            for vn in vn_backrefs:
                vn_uuid = vn['uuid']
                vn_id = {'uuid': vn_uuid}
                (read_ok, read_result) = db_conn.dbe_read('virtual-network',
                                                          vn_id)
                if not read_ok:
                    continue
                if 'virtual_machine_interface_back_refs' in read_result:
                    return True
        return False
    # end is_active_vm_present

# end class NetworkIpamServer


class VirtualDnsServer(VirtualDnsServerGen):
    generate_default_instance = False

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        return cls.validate_dns_server(obj_dict, db_conn)
    # end http_post_collection

    @classmethod
    def http_put(cls, id, fq_name, obj_dict, db_conn):
        return cls.validate_dns_server(obj_dict, db_conn)
    # end http_put

    @classmethod
    def http_put_fail(cls, id, fq_name, obj_dict, db_conn):
        # undo any state change done by http_put function
        return True, ""
    # end http_put_fail

    @classmethod
    def http_delete(cls, id, obj_dict, db_conn):
        vdns_name = ":".join(obj_dict['fq_name'])
        if 'parent_uuid' in obj_dict:
            domain_uuid = obj_dict['parent_uuid']
            domain_id = {'uuid': domain_uuid}
            (read_ok, read_result) = db_conn.dbe_read('domain', domain_id)
            if not read_ok:
                return (
                    False,
                    (500, "Internal error : Virtual DNS is not in a domain"))
            virtual_DNSs = read_result.get('virtual_DNSs', None)
            for vdns in virtual_DNSs:
                vdns_uuid = vdns['uuid']
                vdns_id = {'uuid': vdns_uuid}
                (read_ok, read_result) = db_conn.dbe_read('virtual-DNS',
                                                          vdns_id)
                if not read_ok:
                    return (
                        False,
                        (500,
                         "Internal error : Unable to read Virtual DNS data"))
                vdns_data = read_result['virtual_DNS_data']
                if 'next_virtual_DNS' in vdns_data:
                    if vdns_data['next_virtual_DNS'] == vdns_name:
                        return (
                            False,
                            (403,
                             "Virtual DNS server is referred"
                             " by other virtual DNS servers"))
        return True, ""
    # end http_delete

    @classmethod
    def http_delete_fail(cls, id, obj_dict, db_conn):
        # undo any state change done by http_delete function
        return True, ""
    # end http_delete_fail

    @classmethod
    def is_valid_dns_name(cls, name):
        if len(name) > 255:
            return False
        if name.endswith("."):  # A single trailing dot is legal
            # strip exactly one dot from the right, if present
            name = name[:-1]
        disallowed = re.compile("[^A-Z\d-]", re.IGNORECASE)
        return all(  # Split by labels and verify individually
            (label and len(label) <= 63  # length is within proper range
             # no bordering hyphens
             and not label.startswith("-") and not label.endswith("-")
             and not disallowed.search(label))  # contains only legal char
            for label in name.split("."))
    # end is_valid_dns_name

    @classmethod
    def is_valid_ipv4_address(cls, address):
        parts = address.split(".")
        if len(parts) != 4:
            return False
        for item in parts:
            try:
                if not 0 <= int(item) <= 255:
                    return False
            except ValueError:
                return False
        return True
    # end is_valid_ipv4_address

    @classmethod
    def validate_dns_server(cls, obj_dict, db_conn):
        virtual_dns = obj_dict['fq_name'][1]
        disallowed = re.compile("[^A-Z\d-]", re.IGNORECASE)
        if disallowed.search(virtual_dns) or virtual_dns.startswith("-"):
            return (False, (403,
                    "Special characters are not allowed in " +
                    "Virtual DNS server name"))

        vdns_data = obj_dict['virtual_DNS_data']
        if not cls.is_valid_dns_name(vdns_data['domain_name']):
            return (
                False,
                (403, "Domain name does not adhere to DNS name requirements"))

        record_order = ["fixed", "random", "round-robin"]
        if not str(vdns_data['record_order']).lower() in record_order:
            return (False, (403, "Invalid value for record order"))

        ttl = vdns_data['default_ttl_seconds']
        if ttl < 0 or ttl > 2147483647:
            return (False, (403, "Invalid value for TTL"))

        if 'next_virtual_DNS' in vdns_data:
            vdns_next = vdns_data['next_virtual_DNS']
            if not vdns_next or vdns_next is None:
                return True, ""
            next_vdns = vdns_data['next_virtual_DNS'].split(":")
            # check that next vdns exists
            try:
                next_vdns_uuid = db_conn.fq_name_to_uuid(
                    'virtual_DNS', next_vdns)
            except Exception as e:
                if not cls.is_valid_ipv4_address(
                        vdns_data['next_virtual_DNS']):
                    return (
                        False,
                        (403,
                         "Invalid Virtual Forwarder(next virtual dns server)"))
                else:
                    return True, ""
            # check that next virtual dns servers arent referring to each other
            # above check doesnt allow during create, but entry could be
            # modified later
            next_vdns_id = {'uuid': next_vdns_uuid}
            (read_ok, read_result) = db_conn.dbe_read(
                'virtual-DNS', next_vdns_id)
            if read_ok:
                next_vdns_data = read_result['virtual_DNS_data']
                if 'next_virtual_DNS' in next_vdns_data:
                    vdns_name = ":".join(obj_dict['fq_name'])
                    if next_vdns_data['next_virtual_DNS'] == vdns_name:
                        return (
                            False,
                            (403,
                             "Cannot have Virtual DNS Servers "
                             "referring to each other"))
        return True, ""
    # end validate_dns_server
# end class VirtualDnsServer


class VirtualDnsRecordServer(VirtualDnsRecordServerGen):
    generate_default_instance = False

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        return cls.validate_dns_record(obj_dict, db_conn)
    # end http_post_collection

    @classmethod
    def http_put(cls, id, fq_name, obj_dict, db_conn):
        return cls.validate_dns_record(obj_dict, db_conn)
    # end http_put

    @classmethod
    def http_put_fail(cls, id, fq_name, obj_dict, db_conn):
        # undo any state change done by http_put function
        return True, ""
    # end http_put_fail

    @classmethod
    def http_delete(cls, id, obj_dict, db_conn):
        return True, ""
    # end http_delete

    @classmethod
    def http_delete_fail(cls, id, obj_dict, db_conn):
        # undo any state change done by http_delete function
        return True, ""
    # end http_delete_fail

    @classmethod
    def validate_dns_record(cls, obj_dict, db_conn):
        rec_data = obj_dict['virtual_DNS_record_data']
        rec_types = ["a", "cname", "ptr", "ns"]
        rec_type = str(rec_data['record_type']).lower()
        if not rec_type in rec_types:
            return (False, (403, "Invalid record type"))
        if str(rec_data['record_class']).lower() != "in":
            return (False, (403, "Invalid record class"))

        rec_name = rec_data['record_name']
        rec_value = rec_data['record_data']

        # check rec_name validity
        if rec_type == "ptr":
            if (not VirtualDnsServer.is_valid_ipv4_address(rec_name) and
                    not "in-addr.arpa" in rec_name.lower()):
                return (
                    False,
                    (403,
                     "PTR Record name has to be IP address"
                     " or reverse.ip.in-addr.arpa"))
        elif not VirtualDnsServer.is_valid_dns_name(rec_name):
            return (
                False,
                (403, "Record name does not adhere to DNS name requirements"))

        # check rec_data validity
        if rec_type == "a":
            if not VirtualDnsServer.is_valid_ipv4_address(rec_value):
                return (False, (403, "Invalid IP address"))
        elif rec_type == "cname" or rec_type == "ptr":
            if not VirtualDnsServer.is_valid_dns_name(rec_value):
                return (
                    False,
                    (403,
                     "Record data does not adhere to DNS name requirements"))
        elif rec_type == "ns":
            try:
                vdns_name = rec_value.split(":")
                vdns_uuid = db_conn.fq_name_to_uuid('virtual_DNS', vdns_name)
            except Exception as e:
                if (not VirtualDnsServer.is_valid_ipv4_address(rec_value) and
                        not VirtualDnsServer.is_valid_dns_name(rec_value)):
                    return (
                        False,
                        (403, "Invalid virtual dns server in record data"))

        ttl = rec_data['record_ttl_seconds']
        if ttl < 0 or ttl > 2147483647:
            return (False, (403, "Invalid value for TTL"))
        return True, ""
    # end validate_dns_record
# end class VirtualDnsRecordServer

def _check_policy_rule_uuid(entries):
    if not entries:
        return
    for rule in entries.get('policy_rule') or []:
        if not rule.get('rule_uuid'):
            rule['rule_uuid'] = str(uuid.uuid4())
# end _check_policy_rule_uuid

class SecurityGroupServer(SecurityGroupServerGen):
    generate_default_instance = False

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        try:
            fq_name = obj_dict['fq_name']
            proj_uuid = db_conn.fq_name_to_uuid('project', fq_name[0:2])
        except NoIdError:
            return (False, (500, 'No Project ID error : ' + proj_uuid))

        (ok, proj_dict) = QuotaHelper.get_project_dict(proj_uuid, db_conn)
        if not ok:
            return (False, (500, 'Internal error : ' + pformat(proj_dict)))

        obj_type = 'security-group'
        QuotaHelper.ensure_quota_project_present(obj_type, proj_uuid, proj_dict, db_conn)
        if 'security_groups' in proj_dict:
            quota_count = len(proj_dict['security_groups'])
            (ok, quota_limit) = QuotaHelper.check_quota_limit(proj_dict, obj_type, quota_count)
            if not ok:
                return (False, (403, pformat(obj_dict['fq_name']) + ' : ' + quota_limit))
        return True, ""
    # end http_post_collection

    @classmethod
    def http_put(cls, id, fq_name, obj_dict, db_conn):
        (ok, sec_dict) = QuotaHelper.get_objtype_dict(obj_dict['uuid'], 'security-group', db_conn)
        if not ok:
            return (False, (500, 'Bad Security Group error : ' + pformat(proj_dict)))

        if sec_dict['parent_type'] == 'project':
            (ok, proj_dict) = QuotaHelper.get_project_dict(sec_dict['parent_uuid'], db_conn)
            if not ok:
                return (False, (500, 'Bad Project error : ' + pformat(proj_dict)))

            obj_type = 'security-group-rule'
            QuotaHelper.ensure_quota_project_present(obj_type, proj_dict['uuid'], proj_dict, db_conn)
            if 'security_group_entries' in obj_dict:
                quota_count = len(obj_dict['security_group_entries']['policy_rule'])
                (ok, quota_limit) = QuotaHelper.check_quota_limit(proj_dict, obj_type, quota_count)
                if not ok:
                    return (False, (403, pformat(fq_name) + ' : ' + quota_limit))
        return True, ""
    # end http_put

# end class SecurityGroupServer


class NetworkPolicyServer(NetworkPolicyServerGen):

    @classmethod
    def http_post_collection(cls, tenant_name, obj_dict, db_conn):
        try:
            fq_name = obj_dict['fq_name']
            proj_uuid = db_conn.fq_name_to_uuid('project', fq_name[0:2])
        except NoIdError:
            return (False, (500, 'No Project ID error : ' + proj_uuid))

        (ok, proj_dict) = QuotaHelper.get_project_dict(proj_uuid, db_conn)
        if not ok:
            return (False, (500, 'Internal error : ' + pformat(proj_dict)))

        obj_type = 'network-policy'
        QuotaHelper.ensure_quota_project_present(obj_type, proj_uuid, proj_dict, db_conn)
        if 'network-policys' in proj_dict:
            quota_count = len(proj_dict['network-policys'])
            (ok, quota_limit) = QuotaHelper.check_quota_limit(proj_dict, obj_type, quota_count)
            if not ok:
                return (False, (403, pformat(obj_dict['fq_name']) + ' : ' + quota_limit))

        _check_policy_rule_uuid(obj_dict.get('network_policy_entries'))
        try:
            cls._check_policy(obj_dict)
        except Exception as e:
            return (False, (500, str(e)))

        return True, ""
    # end http_post_collection

    @classmethod
    def http_put(cls, id, fq_name, obj_dict, db_conn):
        p_id = {'uuid': id}
        (read_ok, read_result) = db_conn.dbe_read('network-policy', p_id)
        if not read_ok:
            return (False, (500, read_result))
        _check_policy_rule_uuid(obj_dict.get('network_policy_entries'))
        return True, ""
    # end http_put

    @classmethod
    def _check_policy(cls, obj_dict):
        entries = obj_dict.get('network_policy_entries')
        if not entries:
            return
    # end _check_policy

# end class VirtualNetworkServer
