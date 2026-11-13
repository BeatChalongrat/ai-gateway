"""Fixed sandbox entry point for the template gallery (never runs model code)."""
import json
import sys
from office_templates import create

if __name__ == "__main__":
    try:
        with open(sys.argv[1], encoding="utf-8") as stream:
            request = json.load(stream)
        print(json.dumps(create(request["template"], request["data"], sys.argv[2])))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
