# Optional host fragment; use with the pinned nixpkgs, after provisioning a separate
# Metabase application database and its runtime EnvironmentFile.
{ ... }: {
  services.metabase = {
    enable = true;
    listen.ip = "127.0.0.1";
    listen.port = 3000;
    openFirewall = false;
  };
  # The file contains MB_DB_TYPE=postgres, MB_DB_HOST, MB_DB_PORT,
  # MB_DB_DBNAME=metabase_app, MB_DB_USER and MB_DB_PASS. Never put it in the store.
  systemd.services.metabase.serviceConfig.EnvironmentFile = "/run/secrets/metabase-app-env";
}
