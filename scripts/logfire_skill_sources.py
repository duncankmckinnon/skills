"""Validate the public Logfire skill owners before any upstream sync writes."""

import argparse
import json
import re
from pathlib import Path, PurePosixPath

MANIFEST = Path(__file__).with_name("logfire-skill-sources.json")
LOCAL_REPOSITORY = "pydantic/skills"
REPOSITORIES = {
    "pydantic/logfire",
    "pydantic/logfire-js",
    "pydantic/logfire-cli",
    LOCAL_REPOSITORY,
}


def unique_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate field or owner: {name}")
        result[name] = value
    return result


def parse_sources(contents):
    """Return one unambiguous owner per skill, with safe repository-relative paths."""
    sources = json.loads(contents, object_pairs_hook=unique_fields)
    if not isinstance(sources, dict) or not sources:
        raise ValueError("skill owners must be a non-empty object")
    for name, source in sources.items():
        if not re.fullmatch(r"logfire-[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError(f"invalid skill name: {name}")
        if not isinstance(source, dict) or set(source) != {"repository", "path"}:
            raise ValueError(f"invalid owner fields: {name}")
        repository, path = source["repository"], source["path"]
        if not isinstance(repository, str) or repository not in REPOSITORIES:
            raise ValueError(f"unknown owner repository: {name}")
        if not isinstance(path, str) or not path:
            raise ValueError(f"missing owner path: {name}")
        relative = PurePosixPath(path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or str(relative) != path
            or "\\" in path
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
            or relative.name != name
        ):
            raise ValueError(f"invalid owner path: {name}")
        if repository == LOCAL_REPOSITORY and path != f"plugins/logfire/skills/{name}":
            raise ValueError(f"local owner must be its plugin source: {name}")
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--names", action="store_true")
    selection.add_argument("--external", action="store_true")
    parser.add_argument("manifest", nargs="?", type=Path, default=MANIFEST)
    args = parser.parse_args()
    try:
        sources = parse_sources(args.manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for name, source in sources.items():
        if args.names:
            print(name)
        elif source["repository"] != LOCAL_REPOSITORY:
            print("\t".join((name, source["repository"], source["path"])))


if __name__ == "__main__":
    main()
