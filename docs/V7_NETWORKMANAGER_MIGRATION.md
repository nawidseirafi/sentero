# Sentero v7 – NetworkManager migration

v7 closes the Debian first-install gap where physical network links can be
configured through `/etc/network/interfaces`, causing NetworkManager to report
adapters as `unmanaged` or to run alongside ifupdown.

During `first-install.sh`, Sentero now:

1. backs up `/etc/network/interfaces`, `/etc/network/interfaces.d`, and
   NetworkManager configuration under `/var/backups/sentero-network/<timestamp>`;
2. installs `/etc/NetworkManager/conf.d/10-sentero-managed.conf` with
   `[ifupdown] managed=true`;
3. discovers physical Ethernet and Wi-Fi interfaces independently of
   NetworkManager;
4. removes legacy ifupdown stanzas for those physical appliance interfaces
   while preserving loopback, aliases, bridges, VLANs and other non-physical
   definitions;
5. restarts NetworkManager, forces physical Ethernet/Wi-Fi devices to
   `managed yes`, and fails installation with diagnostics if they remain
   unmanaged.

The host-side Sentero network service can then create `Sentero-Setup-XXXX` on
`192.168.50.1/24` and later switch the same adapter to the customer's WLAN.

Also fixed: AP capability detection no longer treats a failed `iw list` command
as proof that AP mode is supported.
