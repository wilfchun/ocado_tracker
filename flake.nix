{
  description = "Private household purchase analytics";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/1412caf7bf9e660f2f962917c14b1ea1c3bc695e";
  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      each = nixpkgs.lib.genAttrs systems;
    in {
      packages = each (system: let pkgs = import nixpkgs { inherit system; }; in {
        default = pkgs.python3Packages.buildPythonApplication {
          pname = "household-purchases";
          version = "0.1.0";
          pyproject = true;
          src = self;
          build-system = [ pkgs.python3Packages.hatchling ];
          dependencies = with pkgs.python3Packages; [ pydantic sqlalchemy alembic psycopg beautifulsoup4 httpx ];
          nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
          pythonImportsCheck = [ "household_purchases" ];
        };
      });
      devShells = each (system: let pkgs = import nixpkgs { inherit system; }; in {
        default = pkgs.mkShell { packages = [ pkgs.python3 pkgs.uv pkgs.postgresql pkgs.ruff ]; };
      });
    };
}
