# Import alongside this flake's nixosModules.default in an existing NixOS host.
{
  services.household-purchases = {
    enable = true;
    localStack.enable = true;
    # Optional: enables hourly mailbox imports; omit for local EML imports only.
    # fastmailTokenFile = "/run/secrets/fastmail-jmap";
    # localStack.metabase.adminEmail = "your-login@example.invalid";
  };
}
