# Sentero v7 – NetworkManager migration

v7 closes the Debian first-install gap where Wi-Fi can be configured through
`/etc/network/interfaces`, causing NetworkManager to report the adapter as
`unmanaged`.

During `first-install.sh`, Sentero now:

1. backs up `/etc/network/interfaces`, `/etc/network/interfaces.d`, and
   NetworkManager configuration under `/var/backups/sentero-network/<timestamp>`;
2. installs `/etc/NetworkManager/conf.d/10-sentero-managed.conf` with
   `[ifupdown] managed=true`;
3. discovers physical Wi-Fi interfaces independently of NetworkManager;
4. on an offline/fresh box, removes only legacy ifupdown Wi-Fi stanzas while
   preserving loopback and Ethernet definitions;
5. if the installer is currently using Wi-Fi, preserves its legacy stanza for
   that session to reduce the chance of breaking SSH;
6. restarts NetworkManager, forces Wi-Fi devices to `managed yes`, and fails
   installation with diagnostics if they remain unmanaged.

The host-side Sentero network service can then create `Sentero-Setup-XXXX` on
`192.168.50.1/24` and later switch the same adapter to the customer's WLAN.

Also fixed: AP capability detection no longer treats a failed `iw list` command
as proof that AP mode is supported.
