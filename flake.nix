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
      in {
        package = self.packages.${system}.default;
        module = pkgs.runCommand "household-purchases-module-check" {} ''
          test '${evaluated.config.systemd.services.household-purchases-sync.serviceConfig.User}' = household-purchases
          test '${evaluated.config.systemd.services.household-purchases-sync.environment.DATABASE_URL_FILE}' = /run/credentials/household-purchases-sync.service/database-url
          test '${evaluated.config.systemd.timers.household-purchases-sync.timerConfig.OnCalendar}' = hourly
          touch $out
        '';
      });
    };
}
