import json
from pathlib import Path

from api_agent.app import app


def main() -> None:
    output_path = Path(__file__).with_name("swagger.json")
    output_path.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path)


if __name__ == "__main__":
    main()
