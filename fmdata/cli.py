"""CLI tool for fmdata."""
import argparse
import json
import sys


def cmd_status(args):
    from fmdata.registry import list_datasets
    datasets = list_datasets()
    if args.dataset:
        ds = datasets.get(args.dataset)
        if not ds:
            print(f"dataset '{args.dataset}' not found")
            sys.exit(1)
        print(json.dumps(ds, indent=2, ensure_ascii=False))
        return

    print(f"{'Dataset':<30} {'Category':<15} {'Rows':>8} {'Recipe':>7} {'Last Updated':<15}")
    print("-" * 80)
    for name, ds in sorted(datasets.items()):
        cat = ds.get("category", "")
        rows = ds.get("rows", 0)
        has_recipe = "Y" if "recipe" in ds else ""
        updated = str(ds.get("last_updated") or "N/A")
        print(f"{name:<30} {cat:<15} {rows:>8} {has_recipe:>7} {updated:<15}")
    print(f"\nTotal: {len(datasets)} datasets")


def cmd_recipes(args):
    from fmdata.registry import load_all_recipes
    recipes = load_all_recipes()
    if args.name:
        recipe = recipes.get(args.name)
        if not recipe:
            print(f"recipe '{args.name}' not found")
            sys.exit(1)
        import yaml
        print(yaml.dump(recipe, default_flow_style=False, allow_unicode=True))
        return

    print(f"{'Recipe':<30} {'Source':<12} {'Category':<15} {'Freq':<12} {'Description'}")
    print("-" * 95)
    for name, r in sorted(recipes.items()):
        source = r.get("source", "?")
        cat = r.get("category", "?")
        freq = r.get("update_freq", "?")
        desc = (r.get("description") or "")[:40]
        print(f"{name:<30} {source:<12} {cat:<15} {freq:<12} {desc}")


def cmd_fetch(args):
    from fmdata.recipe_fetcher import fetch_dataset, fetch_stale
    if args.dataset == "stale":
        results = fetch_stale(max_age_hours=args.max_age)
        ok = sum(1 for r in results if r.get("status") == "ok")
        err = sum(1 for r in results if r.get("status") == "error")
        print(f"fetched {len(results)} stale datasets: {ok} ok, {err} errors")
        for r in results:
            print(f"  {r['name']}: {r.get('status')} ({r.get('rows', '?')} rows)")
    else:
        result = fetch_dataset(args.dataset)
        print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_init(args):
    from fmdata.registry import init_registry_from_store
    reg = init_registry_from_store()
    print(f"registry initialized: {len(reg['datasets'])} datasets")


def cmd_update(args):
    from fmdata.reference import update_reference
    if args.dataset == "reference":
        update_reference()
        print("reference data updated")
    elif args.dataset == "all":
        update_reference()
        print("reference + all data updated")
    else:
        print(f"update for '{args.dataset}' not yet implemented (use 'reference', 'all', or 'fetch')")


def cmd_serve(args):
    import uvicorn
    from fmdata.config import HTTP_HOST, HTTP_PORT
    uvicorn.run("fmdata.server:app", host=args.host or HTTP_HOST,
                port=args.port or HTTP_PORT, reload=args.reload)


def main():
    parser = argparse.ArgumentParser(description="fmdata — financial data middleware")
    sub = parser.add_subparsers(dest="command")

    # status
    p_status = sub.add_parser("status", help="Show dataset status")
    p_status.add_argument("dataset", nargs="?", help="Specific dataset name")

    # recipes
    p_recipes = sub.add_parser("recipes", help="List or show recipes")
    p_recipes.add_argument("name", nargs="?", help="Specific recipe name")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch dataset using recipe")
    p_fetch.add_argument("dataset", help="Dataset name or 'stale'")
    p_fetch.add_argument("--max-age", type=int, default=24, help="Max age hours for stale (default 24)")

    # init
    sub.add_parser("init", help="Initialize registry from store/")

    # update (legacy)
    p_update = sub.add_parser("update", help="Update datasets (legacy)")
    p_update.add_argument("dataset", help="Dataset name, 'reference', or 'all'")

    # serve
    p_serve = sub.add_parser("serve", help="Start HTTP server")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--reload", action="store_true")

    args = parser.parse_args()
    if args.command == "status":
        cmd_status(args)
    elif args.command == "recipes":
        cmd_recipes(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
