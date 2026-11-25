{ lib, python3Packages, src }:
python3Packages.buildPythonApplication {
  pname = "household-purchases";
  version = "0.1.0";
  pyproject = true;
  inherit src;
  build-system = [ python3Packages.hatchling ];
  dependencies = with python3Packages; [ pydantic sqlalchemy alembic psycopg beautifulsoup4 httpx ];
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  pythonImportsCheck = [ "household_purchases" ];
  meta = { description = "Private household purchase analytics"; mainProgram = "household-purchases"; };
}
