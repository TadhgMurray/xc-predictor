#!/bin/bash
# ============================================================
# setup_mullvad_netns.sh
# ============================================================
#
# PURPOSE:
#   Creates an isolated network namespace called "mullvad".
#   Processes run inside this namespace (via `ip netns exec mullvad ...`)
#   have their OWN network stack — own loopback, own routing table,
#   own interfaces. They are completely separate from the host's
#   network stack.
#
#   We give this namespace internet access via a "veth pair" — think
#   of it like a virtual ethernet cable with one end plugged into the
#   host and the other end plugged into the namespace. Combined with
#   NAT on the host side, the namespace can reach the internet through
#   the host's real network interface.
#
#   Run this ONCE (or after every reboot — it's idempotent, safe to
#   rerun). After this, wg-quick can bring up a WireGuard tunnel
#   INSIDE the namespace, and only processes launched inside it will
#   use that tunnel.
#
# WHY THIS FIXES THE SSH/MULLVAD PROBLEM:
#   The host's nftables/routing is never touched. SSH, sshd, the
#   cloud-sql-proxy on the host, etc. all keep using the host's normal
#   network — Mullvad's global firewall rules never apply to them,
#   because Mullvad's WireGuard interface and rules only exist INSIDE
#   the "mullvad" namespace.
 
set -e   # Exit immediately if any command fails — safer for setup scripts

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

NETNS_NAME="mullvad"
 
# veth pair — one end stays on the host, one end goes into the namespace.
VETH_HOST="veth-host"
VETH_NS="veth-ns"
 
# IP addresses for the veth pair. /30 subnet = only these 2 usable IPs.
# Arbitrary private IPs — just need to not collide with anything else.
# It's like running an eternet (virtual ethernet) between the regular network
# namespace and the mullvad one.
VETH_HOST_IP="10.200.200.1/30"
VETH_NS_IP="10.200.200.2/30"
 
# The real interface on the host that has internet access.
# On most GCP VMs this is "ens4" or "eth0" — check with `ip a`.
HOST_INTERFACE="ens4"

# ------------------------------------------------------------------ #
# HELPER FUNCTIONS
# ------------------------------------------------------------------ #

# _createNamespace
# Purpose: Creates the network namespace if it doesn't already exist.
# Arguments: None.
# Output: None. Namespace "mullvad" exists after this runs.
_createNamespace() {

    # `ip netns add` errors if it already exists — check first so
    # this script is safe to rerun (idempotent).
    if ! ip netns list | grep -q "$NETNS_NAME"; then
        ip netns add "$NETNS_NAME"
        echo "Created namespace: $NETNS_NAME"
    else
        echo "Namespace $NETNS_NAME already exists, skipping"
    # How bash closes an if blocck.
    fi
}

# _createVethPair
# Purpose: Creates the virtual ethernet cable connecting host <-> namespace,
#          assigns IPs to each end, and brings both ends up.
# Arguments: None.
# Output: None. veth-host exists on host, veth-ns exists inside namespace.
_createVethPair() {

    # Skip if already created (idempotent).
    if ip link show "$VETH_HOST" &>/dev/null; then
        echo "veth pair already exists, skipping"
        return
    fi

    # Creates a connected pair of virtual interfaces. Packets
    # sent into one end come out the other.
    ip link add "$VETH_HOST" type veth peer name "$VETH_NS"

    # Move one end of the cabel into the namespace
    ip link set "$VETH_NS" netns "$NETNS_NAME"

    # Assign IP to the host end and bring it up.
    ip addr add "$VETH_HOST_IP" dev "$VETH_HOST"
    ip link set "$VETH_HOST" up

    # Assign IP to the namespace end and bring it up.
    # `ip netns exec mullvad <command>` runs <command> inside the namespace.
    ip netns exec "$NETNS_NAME" ip addr add "$VETH_NS_IP" dev "$VETH_NS"
    ip netns exec "$NETNS_NAME" ip link set "$VETH_NS" up

    # Bring up the namespace's loopback interface too — many programs
    # (including cloud-sql-proxy) expect 127.0.0.1 to work.
    ip netns exec "$NETNS_NAME" ip link set lo up
 
    echo "veth pair created: $VETH_HOST <-> $VETH_NS"
}

# _setDefaultRoute
# Purpose: Tells the namespace "send all traffic with no better route
#          to the host, via the veth cable." Without this, the
#          namespace has no way to reach the internet at all.
# Arguments: None.
# Output: None.
_setDefaultRoute() {
    # 10.200.200.1 is the HOST side of the veth pair (defined above).
    # This becomes the namespace's gateway.
    ip netns exec "$NETNS_NAME" ip route add default via 10.200.200.1 \
        2>/dev/null || echo "Default route already set, skipping"
}

# _enableNAT
# Purpose: Lets the host forward and NAT traffic coming from the
#          namespace out through the real internet interface.
#          Without this, packets from the namespace reach the host
#          but the host doesn't know what to do with them.
# Arguments: None.
# Output: None.
_enableNAT() {
    # Allow the kernel to forward packets between interfaces at all.
    # Without this, the host drops packets not addressed to itself.
    echo 1 > /proc/sys/net/ipv4/ip_forward
 
    # MASQUERADE rewrites the source IP of outgoing packets to the
    # host's own IP on HOST_INTERFACE — this is standard NAT, same
    # as what your home router does for every device on your WiFi.
    # -C checks if the rule already exists (idempotent); if not, -A adds it.
    iptables -t nat -C POSTROUTING -s 10.200.200.0/30 -o "$HOST_INTERFACE" -j MASQUERADE 2>/dev/null \
        || iptables -t nat -A POSTROUTING -s 10.200.200.0/30 -o "$HOST_INTERFACE" -j MASQUERADE
 
    echo "NAT enabled for namespace traffic"
}

# ------------------------------------------------------------------ #
# MAIN
# ------------------------------------------------------------------ #
 
_createNamespace
_createVethPair
_setDefaultRoute
_enableNAT
 
echo ""
echo "Setup complete. Test with:"
echo "  sudo ip netns exec $NETNS_NAME ping -c 3 1.1.1.1"