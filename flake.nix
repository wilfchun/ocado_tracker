{
  description = "Private household purchase analytics";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/1412caf7bf9e660f2f962917c14b1ea1c3bc695e";
  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      each = nixpkgs.lib.genAttrs systems;
    in {
      packages = each (system: let pkgs = import nixpkgs { inherit system; }; in {
        default = pkgs.callPackage ./nix/package.nix { src = self; };
      });
      devShells = each (system: let pkgs = import nixpkgs { inherit system; }; in {
        default = pkgs.mkShell { packages = [ pkgs.python3 pkgs.uv pkgs.postgresql pkgs.ruff ]; };
      });
      nixosModules.default = { lib, pkgs, ... }: {
        imports = [ ./nix/module.nix ];
        services.household-purchases.package = lib.mkDefault self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        services.household-purchases.localStack.metabase.package = lib.mkDefault
          (import nixpkgs { system = pkgs.stdenv.hostPlatform.system; }).metabase;
      };
      checks = each (system: let
        pkgs = import nixpkgs { inherit system; };
        evaluated = nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [ self.nixosModules.default {
            services.household-purchases = {
              enable = true;
              databaseUrlFile = "/run/secrets/purchases-db";
              fastmailTokenFile = "/run/secrets/fastmail";
              llm.enable = true;
              llm.model = "local-model";
            };
            system.stateVersion = "25.11";
          } ];
        };
        stack = nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [ self.nixosModules.default {
            services.household-purchases = {
              enable = true;
              localStack.enable = true;
            };
            system.stateVersion = "25.11";
          } ];
        };
      in {
        package = self.packages.${system}.default;
        local-stack = pkgs.runCommand "household-purchases-local-stack-check" {} ''
          test -x ${stack.config.services.household-purchases.localStack.helperPackage}/bin/household-purchases-local
          test '${toString stack.config.services.postgresql.enable}' = 1
          test '${stack.config.services.metabase.listen.ip}' = 127.0.0.1
          test '${stack.config.systemd.services.metabase.serviceConfig.EnvironmentFile}' = /var/lib/household-purchases-provision/metabase.env
          test '${stack.config.systemd.services.household-purchases-provision.serviceConfig.User}' = postgres
          test '${stack.config.systemd.services.household-purchases-classify.environment.DATABASE_URL}' = 'postgresql+psycopg://purchases_importer@/household_purchases?host=%2Frun%2Fpostgresql&port=5432'
          test '${toString (builtins.hasAttr "household-purchases-sync" stack.config.systemd.services)}' = ""
          test '${toString (builtins.elem "household-purchases-provision.service" stack.config.systemd.services.metabase.requires)}' = 1
          test '${toString (builtins.elem "household-purchases-provision.service" stack.config.systemd.services.household-purchases-classify.requires)}' = 1
          touch $out
        '';
        module = pkgs.runCommand "household-purchases-module-check" {} ''
          test '${evaluated.config.systemd.services.household-purchases-sync.serviceConfig.User}' = household-purchases
          test '${evaluated.config.systemd.services.household-purchases-sync.environment.DATABASE_URL_FILE}' = /run/credentials/household-purchases-sync.service/database-url
          test '${evaluated.config.systemd.timers.household-purchases-sync.timerConfig.OnCalendar}' = hourly
          touch $out
        '';
      });
    };
}
