{ config, lib, pkgs, ... }:
let
  cfg = config.services.household-purchases;
  stack = cfg.localStack;
  inherit (lib) mkOption mkEnableOption mkIf mkDefault types;
  stateDir = "/var/lib/household-purchases-provision";
  port = config.services.postgresql.settings.port;
  provisionEnv = {
    HP_PROVISION_STATE_DIR = stateDir;
    HP_DATABASE_NAME = stack.databaseName;
    HP_METABASE_DATABASE = stack.metabase.databaseName;
    HP_POSTGRES_SOCKET = "/run/postgresql";
    HP_POSTGRES_PORT = toString port;
    HP_METABASE_ENABLED = lib.boolToString stack.metabase.enable;
  };
  localCli = pkgs.writeShellApplication {
    name = "household-purchases-local";
    runtimeInputs = [ pkgs.util-linux pkgs.coreutils ];
    text = ''
      if [ "$(id -u)" != 0 ]; then
        echo 'Run with sudo. For private email files: sudo household-purchases-local import-eml /dev/stdin < file.eml' >&2
        exit 1
      fi
      case "''${1:-}" in
        import-eml|status|classify|retry-failed|reprocess|categories|products) ;;
        *) echo 'Use local import/status/catalogue commands; use systemctl for sync and provisioning.' >&2; exit 1 ;;
      esac
      exec runuser -u household-purchases -- env -u DATABASE_URL_FILE \
        DATABASE_URL=${lib.escapeShellArg stack.importerUrl} \
        RAW_EMAIL_DIR=${lib.escapeShellArg "${cfg.dataDir}/raw"} \
        TIMEZONE=${lib.escapeShellArg cfg.timezone} \
        ${lib.getExe cfg.package} "$@"
    '';
  };
in {
  options.services.household-purchases.localStack = {
    enable = mkEnableOption "automatic local PostgreSQL, migrations and optional Metabase provisioning";
    databaseName = mkOption { type = types.strMatching "[a-z][a-z0-9_]{0,62}"; default = "household_purchases"; };
    importerUrl = mkOption {
      type = types.str;
      readOnly = true;
      default = "postgresql+psycopg://purchases_importer@/${stack.databaseName}?host=%2Frun%2Fpostgresql&port=${toString port}";
      description = "Non-secret local peer-authenticated importer connection.";
    };
    helperPackage = mkOption { type = types.package; readOnly = true; internal = true; default = localCli; };
    metabase = {
      package = mkOption { type = types.package; default = pkgs.metabase; description = "Metabase package; pinned by the flake module."; };
      enable = mkOption { type = types.bool; default = true; };
      databaseName = mkOption { type = types.strMatching "[a-z][a-z0-9_]{0,62}"; default = "household_purchases_metabase"; };
      bootstrap = mkOption { type = types.bool; default = true; description = "Create the initial admin, read-only data source and starter dashboard."; };
      adminEmail = mkOption { type = types.str; default = "admin@example.invalid"; description = "Login name for the initial managed Metabase administrator."; };
    };
  };
  config = mkIf (cfg.enable && stack.enable) {
    assertions = [
      { assertion = stack.databaseName != stack.metabase.databaseName; message = "Purchase facts and Metabase application state need different databases"; }
      { assertion = lib.all (name: !(builtins.elem name [ "postgres" "template0" "template1" ])) [ stack.databaseName stack.metabase.databaseName ]; message = "Choose dedicated databases for household-purchases"; }
      { assertion = config.services.postgresql.settings.unix_socket_directories == "/run/postgresql"; message = "localStack requires the standard /run/postgresql socket directory"; }
      { assertion = !stack.metabase.enable || config.services.metabase.listen.ip == "127.0.0.1"; message = "Keep managed Metabase on loopback; use an authenticated reverse proxy or SSH tunnel"; }
    ];
    services.postgresql = {
      enable = true;
      enableTCPIP = mkDefault false;
      settings.unix_socket_directories = mkDefault "/run/postgresql";
      authentication = lib.mkBefore ''
        local ${stack.databaseName} purchases_owner peer map=household-purchases
        local ${stack.databaseName} purchases_importer peer map=household-purchases
        host ${stack.databaseName} purchases_reader 127.0.0.1/32 scram-sha-256
        host ${stack.databaseName} purchases_reader 0.0.0.0/0 reject
        host ${stack.databaseName} purchases_reader ::/0 reject
        host ${stack.metabase.databaseName} purchases_metabase 127.0.0.1/32 scram-sha-256
        host ${stack.metabase.databaseName} purchases_metabase 0.0.0.0/0 reject
        host ${stack.metabase.databaseName} purchases_metabase ::/0 reject
      '';
      identMap = ''
        household-purchases postgres purchases_owner
        household-purchases household-purchases purchases_importer
      '';
    };
    environment.systemPackages = [ cfg.package localCli ];
    systemd.services.household-purchases-provision = {
      description = "Provision household purchase databases and apply migrations";
      wantedBy = [ "multi-user.target" ];
      requires = [ "postgresql.service" "postgresql-setup.service" ];
      after = [ "postgresql.service" "postgresql-setup.service" ];
      before = [ "household-purchases-sync.service" "household-purchases-classify.service" ] ++ lib.optional stack.metabase.enable "metabase.service";
      environment = provisionEnv;
      restartTriggers = [ cfg.package ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "postgres";
        Group = "postgres";
        StateDirectory = "household-purchases-provision";
        StateDirectoryMode = "0700";
        UMask = "0077";
        ExecStart = "${cfg.package}/bin/household-purchases-provision";
        TimeoutStartSec = "10min";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      };
    };
    services.metabase = mkIf stack.metabase.enable {
      enable = true;
      package = stack.metabase.package;
      listen.ip = mkDefault "127.0.0.1";
      openFirewall = false;
    };
    systemd.services.metabase = mkIf stack.metabase.enable {
      requires = [ "household-purchases-provision.service" ];
      after = [ "household-purchases-provision.service" ];
      partOf = [ "household-purchases-provision.service" ];
      serviceConfig.EnvironmentFile = "${stateDir}/metabase.env";
    };
    systemd.services.household-purchases-metabase-setup = mkIf (stack.metabase.enable && stack.metabase.bootstrap) {
      description = "Set up local Metabase admin, analytics connection and dashboard";
      wantedBy = [ "multi-user.target" ];
      requires = [ "metabase.service" ];
      after = [ "metabase.service" ];
      partOf = [ "metabase.service" ];
      environment = provisionEnv // {
        HP_METABASE_PORT = toString config.services.metabase.listen.port;
        HP_METABASE_ADMIN_EMAIL = stack.metabase.adminEmail;
      };
      restartTriggers = [ cfg.package ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "postgres";
        Group = "postgres";
        UMask = "0077";
        ExecStart = "${cfg.package}/bin/household-purchases-metabase-setup";
        TimeoutStartSec = "10min";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      };
    };
  };
}
