{ config, lib, ... }:
let
  cfg = config.services.household-purchases;
  inherit (lib) mkOption mkEnableOption types mkIf optionalAttrs;
  credentials = [ "database-url:${cfg.databaseUrlFile}" ];
  baseEnvironment = {
    RAW_EMAIL_DIR = "${cfg.dataDir}/raw";
    TIMEZONE = cfg.timezone;
    SOURCE_ACCOUNT = cfg.sourceAccount;
    OCADO_SENDER = cfg.sender;
    OCADO_SUBJECT = cfg.subject;
    LOG_LEVEL = "INFO";
  } // optionalAttrs (cfg.recipient != null) { OCADO_RECIPIENT = cfg.recipient; }
    // optionalAttrs (cfg.mailboxId != null) { OCADO_MAILBOX_ID = cfg.mailboxId; }
    // optionalAttrs (cfg.accountId != null) { FASTMAIL_ACCOUNT_ID = cfg.accountId; };
  service = name: {
    wants = [ "network-online.target" ];
    after = [ "network-online.target" "postgresql.service" ];
    environment = baseEnvironment // {
      DATABASE_URL_FILE = "/run/credentials/${name}.service/database-url";
    };
    serviceConfig = {
      Type = "oneshot";
      User = "household-purchases";
      Group = "household-purchases";
      UMask = "0077";
      WorkingDirectory = cfg.dataDir;
      LoadCredential = credentials;
      TimeoutStartSec = "45min";
      NoNewPrivileges = true;
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ReadWritePaths = [ cfg.dataDir ];
      PrivateDevices = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictSUIDSGID = true;
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      CapabilityBoundingSet = "";
      LockPersonality = true;
    };
  };
in {
  options.services.household-purchases = {
    enable = mkEnableOption "private household purchase importer";
    package = mkOption { type = types.package; description = "Importer package (provided by the flake module)."; };
    dataDir = mkOption { type = types.str; default = "/var/lib/household-purchases"; };
    databaseUrlFile = mkOption { type = types.str; description = "Runtime path to database URL secret; never a Nix store path."; };
    fastmailTokenFile = mkOption { type = types.str; description = "Runtime path to read-only JMAP token."; };
    syncSchedule = mkOption { type = types.str; default = "hourly"; };
    classificationSchedule = mkOption { type = types.nullOr types.str; default = "daily"; };
    timezone = mkOption { type = types.str; default = "Europe/London"; };
    sourceAccount = mkOption { type = types.str; default = "fastmail"; };
    sender = mkOption { type = types.str; default = "noreply@email.ocado.com"; };
    subject = mkOption { type = types.str; default = "Your upcoming Ocado delivery"; };
    recipient = mkOption { type = types.nullOr types.str; default = null; };
    mailboxId = mkOption { type = types.nullOr types.str; default = null; };
    accountId = mkOption { type = types.nullOr types.str; default = null; };
    llm = {
      enable = mkEnableOption "optional local classification";
      baseUrl = mkOption { type = types.str; default = "http://127.0.0.1:8080/v1"; };
      model = mkOption { type = types.str; default = ""; };
      apiKeyFile = mkOption { type = types.nullOr types.str; default = null; };
    };
  };
  config = mkIf cfg.enable {
    assertions = [
      { assertion = !cfg.llm.enable || cfg.llm.model != ""; message = "household-purchases.llm.model is required when enabled"; }
      { assertion = lib.hasPrefix "/" cfg.dataDir; message = "household-purchases.dataDir must be absolute"; }
      { assertion = lib.all (p: lib.hasPrefix "/" p && !(lib.hasPrefix "/nix/store/" p))
          ([ cfg.databaseUrlFile cfg.fastmailTokenFile ] ++ lib.optional (cfg.llm.apiKeyFile != null) cfg.llm.apiKeyFile);
        message = "household-purchases credentials must be runtime absolute paths outside the Nix store"; }
    ];
    users.groups.household-purchases = {};
    users.users.household-purchases = { isSystemUser = true; group = "household-purchases"; home = cfg.dataDir; };
    systemd.tmpfiles.rules = [ "d ${cfg.dataDir} 0700 household-purchases household-purchases -" ];
    systemd.services.household-purchases-sync = lib.recursiveUpdate (service "household-purchases-sync") {
      description = "Import household delivery summaries (read-only mailbox)";
      environment.FASTMAIL_TOKEN_FILE = "/run/credentials/household-purchases-sync.service/fastmail-token";
      serviceConfig = {
        LoadCredential = credentials ++ [ "fastmail-token:${cfg.fastmailTokenFile}" ];
        ExecStart = "${lib.getExe cfg.package} sync";
      };
    };
    systemd.timers.household-purchases-sync = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnCalendar = cfg.syncSchedule; Persistent = true; RandomizedDelaySec = "5min"; };
    };
    systemd.services.household-purchases-classify = lib.recursiveUpdate (service "household-purchases-classify") {
      description = "Classify household product catalogue";
      environment = {
        LLM_ENABLED = lib.boolToString cfg.llm.enable;
        LLM_BASE_URL = cfg.llm.baseUrl;
        LLM_MODEL = cfg.llm.model;
      } // optionalAttrs (cfg.llm.apiKeyFile != null) {
        LLM_API_KEY_FILE = "/run/credentials/household-purchases-classify.service/llm-key";
      };
      serviceConfig = {
        LoadCredential = credentials ++ lib.optional (cfg.llm.apiKeyFile != null) "llm-key:${cfg.llm.apiKeyFile}";
        ExecStart = "${lib.getExe cfg.package} classify --limit 100";
      };
    };
    systemd.timers.household-purchases-classify = mkIf (cfg.classificationSchedule != null) {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnCalendar = cfg.classificationSchedule; Persistent = true; };
    };
  };
}
