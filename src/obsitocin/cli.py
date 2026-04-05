#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path

from obsitocin.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DATA_DIR,
    get_config_validation_errors,
    LOGS_DIR,
    PROCESSED_DIR,
    QUEUE_DIR,
)

GLOBAL_SKILL_DIR = Path.home() / ".claude" / "skills" / "vault-search"
SKILL_SOURCE = (
    Path(__file__).parent.parent.parent
    / ".claude"
    / "skills"
    / "vault-search"
    / "SKILL.md"
)
HOOK_RUNTIME_DIR = DATA_DIR / "runtime"


def _hook_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _ensure_hook_runtime() -> Path:
    venv_dir = HOOK_RUNTIME_DIR / "venv"
    python_bin = _hook_python_path(venv_dir)
    if python_bin.exists():
        return python_bin

    HOOK_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    if not python_bin.exists():
        raise RuntimeError(
            f"Hook runtime python not found after bootstrap: {python_bin}"
        )

    return python_bin


def _echo(message: str = "") -> None:
    print(message)


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def _prompt_text(label: str) -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        return input(label).strip()
    except EOFError:
        return ""


def _confirm(label: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{label} [y/N]: ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _report_config_validation() -> None:
    errors = get_config_validation_errors()
    if not errors:
        return
    _echo("Config warnings:")
    for error in errors:
        _echo(f"  - {error}")
    _echo()


def _install_skill() -> None:
    GLOBAL_SKILL_DIR.mkdir(parents=True, exist_ok=True)
    dest = GLOBAL_SKILL_DIR / "SKILL.md"
    if SKILL_SOURCE.exists():
        dest.write_text(SKILL_SOURCE.read_text())
        return
    dest.write_text(
        "---\n"
        "name: vault-search\n"
        "description: Search the Claude knowledge graph vault for past Q&A sessions, concepts, and developer knowledge. Use when you need to find information from previous conversations, recall how something was done before, or look up stored technical knowledge. Supports Korean and English queries.\n"
        "argument-hint: [search query]\n"
        "allowed-tools: Bash(obsitocin query *)\n"
        "user-invocable: true\n"
        "---\n\n"
        "# Vault Search\n\n"
        "Search your knowledge graph vault using the local memory index.\n\n"
        "## Search Query: $ARGUMENTS\n\n"
        '!`obsitocin query --context "$ARGUMENTS" --top-k 5`\n'
    )


def _uninstall_skill() -> None:
    skill_file = GLOBAL_SKILL_DIR / "SKILL.md"
    if skill_file.exists():
        skill_file.unlink()
    try:
        GLOBAL_SKILL_DIR.rmdir()
    except OSError:
        pass


def _check_dependencies(llm_provider: str | None = None) -> None:
    import shutil

    from obsitocin.config import (
        CLAUDE_CLI_BIN,
        CLAUDE_MODEL,
        CODEX_CLI_BIN,
        CODEX_MODEL,
        EMBED_MODEL_PATH,
        EMBED_PORT,
        GEMINI_CLI_BIN,
        GEMINI_MODEL,
        LLM_PROVIDER,
        LLAMA_SERVER_BIN,
        QWEN_MODEL_PATH,
        QWEN_PORT,
    )
    from obsitocin.provider import get_provider

    active = llm_provider or LLM_PROVIDER
    config = _load_config()
    config_changed = False

    provider_info = get_provider(active)

    all_providers = {
        "codex": {"bin": CODEX_CLI_BIN, "model": CODEX_MODEL, "label": "Codex CLI"},
        "claude": {
            "bin": CLAUDE_CLI_BIN,
            "model": CLAUDE_MODEL,
            "label": "Claude CLI",
        },
        "gemini": {
            "bin": GEMINI_CLI_BIN,
            "model": GEMINI_MODEL,
            "label": "Gemini CLI",
        },
        "qwen": {
            "bin": LLAMA_SERVER_BIN,
            "model": QWEN_MODEL_PATH.name if QWEN_MODEL_PATH != Path("") else "qwen",
            "label": "Local Qwen",
        },
    }

    for name, info in all_providers.items():
        path = Path(info["bin"])
        is_active = name == active
        status_label = "ACTIVE" if is_active else "available"

        if path.exists() or shutil.which(str(info["bin"])):
            _echo(f"{info['label']}: found ({info['bin']}) [{status_label}]")
        else:
            if is_active:
                _echo(f"{info['label']}: NOT FOUND")
                prompt_label = "llama-server" if name == "qwen" else info["label"]
                user_path = _prompt_text(
                    f"  Enter {prompt_label} path (or press Enter to skip): "
                )
                if user_path:
                    resolved = str(Path(user_path).expanduser().resolve())
                    if Path(resolved).exists():
                        config_key = "llama_server" if name == "qwen" else f"{name}_cli"
                        config[config_key] = resolved
                        config_changed = True
                        _echo(f"  {prompt_label} path saved ({resolved})")
                    else:
                        _echo(f"  Warning: {resolved} does not exist, skipping.")
                else:
                    if name == "qwen":
                        _echo(
                            "  Install llama.cpp and configure a local Qwen GGUF model."
                        )
                    else:
                        _echo(
                            f"  Install {info['label']} and authenticate once before running obsitocin."
                        )
            else:
                _echo(f"{info['label']}: optional, not configured")

    _echo(f"Active provider: {active}")
    _echo(f"Active model: {provider_info.model}")

    if active == "qwen":
        qwen_model_path = Path(QWEN_MODEL_PATH)
        if QWEN_MODEL_PATH != Path("") and qwen_model_path.exists():
            _echo(f"Qwen model: found ({qwen_model_path.name})")
            _echo(f"Qwen server port: {QWEN_PORT}")
        else:
            _echo("Qwen model: not configured (local tagging will fail until set)")
            user_path = _prompt_text(
                "  Enter Qwen GGUF path (or press Enter to skip): "
            )
            if user_path:
                resolved = str(Path(user_path).expanduser().resolve())
                if Path(resolved).exists():
                    config["qwen_model_path"] = resolved
                    config_changed = True
                    _echo(f"  Qwen model saved ({Path(resolved).name})")
                else:
                    _echo(f"  Warning: {resolved} does not exist, skipping.")
            else:
                _echo(
                    "  Download: hf download unsloth/Qwen3.5-4B-GGUF --include '*Q4_K_M*'"
                )

    llama_server_path = Path(LLAMA_SERVER_BIN)
    if llama_server_path.exists() or shutil.which(str(LLAMA_SERVER_BIN)):
        _echo(f"llama-server: found ({LLAMA_SERVER_BIN})")
    else:
        _echo("llama-server: NOT FOUND (semantic embedding search unavailable)")
        user_path = _prompt_text("  Enter llama-server path (or press Enter to skip): ")
        if user_path:
            resolved = str(Path(user_path).expanduser().resolve())
            if Path(resolved).exists():
                config["llama_server"] = resolved
                config_changed = True
                _echo(f"  llama-server path saved ({resolved})")
            else:
                _echo(f"  Warning: {resolved} does not exist, skipping.")
        else:
            _echo("  Install: brew install llama.cpp  (macOS)")
            _echo("  Or build from source: https://github.com/ggml-org/llama.cpp")

    embed_model_path = Path(EMBED_MODEL_PATH)
    if EMBED_MODEL_PATH != Path("") and embed_model_path.exists():
        _echo(f"Embedding model: found ({embed_model_path.name})")
        _echo(f"Embedding server port: {EMBED_PORT}")
    else:
        _echo(
            "Embedding model: not configured (query/embed commands will fail until set)"
        )
        user_path = _prompt_text(
            "  Enter embedding GGUF path (or press Enter to skip): "
        )
        if user_path:
            resolved = str(Path(user_path).expanduser().resolve())
            if Path(resolved).exists():
                config["embed_model_path"] = resolved
                config_changed = True
                _echo(f"  Embedding model saved ({Path(resolved).name})")
            else:
                _echo(f"  Warning: {resolved} does not exist, skipping.")
        else:
            _echo("  Download: pip install huggingface-hub")
            _echo("  hf download Qwen/Qwen3-Embedding-0.6B-GGUF --include '*Q8_0*'")

    if config_changed:
        _save_config(config)


def _cmd_init(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault_dir).expanduser().resolve()
    config = _load_config()
    config["vault_dir"] = str(vault_path)

    if args.llm_provider:
        from obsitocin.config import VALID_LLM_PROVIDERS

        if args.llm_provider not in VALID_LLM_PROVIDERS:
            print(
                f"Error: invalid provider '{args.llm_provider}'. "
                f"Choose from: {', '.join(VALID_LLM_PROVIDERS)}",
                file=sys.stderr,
            )
            return 1
        config["llm_provider"] = args.llm_provider

    _save_config(config)
    _echo(f"Config saved: {CONFIG_FILE}")

    for directory in (QUEUE_DIR, PROCESSED_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    _echo(f"Data directory: {DATA_DIR}")

    kg_dir = vault_path / "obsitocin"
    for sub in ("projects", "daily"):
        (kg_dir / sub).mkdir(parents=True, exist_ok=True)
    _echo(f"Knowledge graph directory: {kg_dir}")

    from obsitocin.hooks import register_hooks

    try:
        hook_python = _ensure_hook_runtime()
    except (subprocess.CalledProcessError, RuntimeError) as error:
        print(
            "Error: failed to bootstrap the hook runtime. "
            "Make sure the current Python can create virtual environments.",
            file=sys.stderr,
        )
        print(f"Detail: {error}", file=sys.stderr)
        return 1

    _echo(f"Hook runtime: {hook_python}")

    if register_hooks(str(hook_python)):
        _echo("Claude Code hooks registered.")
    else:
        _echo("Claude Code hooks already registered.")

    _install_skill()
    _echo("vault-search skill installed globally.")
    _echo()
    _check_dependencies(args.llm_provider if hasattr(args, "llm_provider") else None)
    _report_config_validation()
    _echo()
    _echo("Setup complete! Claude Code will now capture Q&A pairs automatically.")
    _echo(
        "Run 'obsitocin run' to process pending entries, or they'll be processed automatically."
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from obsitocin.processor import main as run_processor, preview_pending_run

    provider_name = getattr(args, "llm_provider", None)
    if getattr(args, "dry_run", False):
        preview = preview_pending_run(
            provider_name=provider_name,
            pii_enabled=args.detect_pii,
            pii_redact=args.redact_pii,
            pii_skip_sensitive=args.skip_sensitive,
        )
        _echo(f"Dry run provider: {preview['provider']}")
        _echo(f"Pending files: {preview['pending']}")
        for detail in preview["details"]:
            if detail["action"] == "duplicate":
                _echo(
                    f"  - {detail['file']}: duplicate (matches {detail['duplicate_of']})"
                )
            else:
                _echo(f"  - {detail['file']}: {detail['action']}")
        _report_config_validation()
        return 0
    run_processor(
        provider_name=provider_name,
        pii_enabled=args.detect_pii,
        pii_redact=args.redact_pii,
        pii_skip_sensitive=args.skip_sensitive,
    )
    _report_config_validation()
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    counts = {
        "pending": 0,
        "processed": 0,
        "written": 0,
        "skipped": 0,
        "duplicate": 0,
    }

    for directory in (QUEUE_DIR, PROCESSED_DIR):
        if not directory.exists():
            continue
        for file_path in directory.glob("*.json"):
            if file_path.stem.endswith("_prompt"):
                continue
            try:
                data = json.loads(file_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            status = data.get("status", "unknown")
            if status in counts:
                counts[status] += 1

    _echo(f"Pending:   {counts['pending']}")
    _echo(f"Processed: {counts['processed']}")
    _echo(f"Written:   {counts['written']}")
    _echo(f"Skipped:   {counts['skipped']}")
    _echo(f"Duplicate: {counts['duplicate']}")
    _echo(f"Total:     {sum(counts.values())}")
    _echo()

    from obsitocin.hooks import check_hooks

    hook_status = check_hooks()
    all_ok = all(hook_status.values())
    _echo(f"Hooks: {'all registered' if all_ok else 'MISSING'}")
    if not all_ok:
        for event, registered in hook_status.items():
            if not registered:
                _echo(f"  {event}: not registered (run 'obsitocin init' to fix)")
    _report_config_validation()
    return 0


def _cmd_verify(_: argparse.Namespace) -> int:
    from obsitocin.maintenance import verify_state

    report = verify_state()
    duplicate_count = len(report["duplicate_content_hashes"])
    issues = (
        sum(
            len(report[key])
            for key in (
                "queue_invalid",
                "processed_invalid",
                "missing_tagging_result",
                "missing_content_hash",
                "orphan_embeddings",
            )
        )
        + duplicate_count
    )

    if issues == 0:
        _echo("Verification passed. No issues found.")
        _report_config_validation()
        return 0

    _echo("Verification issues:")
    for key in (
        "queue_invalid",
        "processed_invalid",
        "missing_tagging_result",
        "missing_content_hash",
        "orphan_embeddings",
    ):
        values = report[key]
        if values:
            _echo(f"- {key}: {len(values)}")
            for value in values[:10]:
                _echo(f"    {value}")
    if report["duplicate_content_hashes"]:
        _echo(f"- duplicate_content_hashes: {duplicate_count}")
        for content_hash, files in sorted(report["duplicate_content_hashes"].items())[
            :10
        ]:
            _echo(f"    {content_hash}: {', '.join(files)}")
    _report_config_validation()
    return 1


def _cmd_cleanup(args: argparse.Namespace) -> int:
    from obsitocin.maintenance import cleanup_state

    result = cleanup_state(dry_run=args.dry_run)
    prefix = "Would remove" if args.dry_run else "Removed"
    _echo(f"{prefix} orphan prompts: {len(result['orphan_prompts'])}")
    for value in result["orphan_prompts"][:10]:
        _echo(f"  - {value}")
    _echo(f"{prefix} orphan embeddings: {len(result['orphan_embeddings'])}")
    for value in result["orphan_embeddings"][:10]:
        _echo(f"  - {value}")
    _report_config_validation()
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    filters = {}
    if args.memory_type:
        filters["memory_type"] = args.memory_type
    if args.category:
        filters["category"] = args.category
    if args.importance_min is not None:
        filters["importance_min"] = args.importance_min

    try:
        if args.context_mode:
            from obsitocin.memory_query import get_context

            _echo(get_context(args.query_text, top_k=args.top_k))
        else:
            from obsitocin.memory_query import format_results_table, query

            results = query(args.query_text, top_k=args.top_k, filters=filters or None)
            _echo(f'\nResults for: "{args.query_text}"\n')
            _echo(format_results_table(results))
        _report_config_validation()
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def _cmd_concepts(args: argparse.Namespace) -> int:
    try:
        from obsitocin.memory_query import (
            format_concept_results_table,
            query_concepts,
        )

        results = query_concepts(args.query_text, top_k=args.top_k)
        _echo(f'\nConcepts for: "{args.query_text}"\n')
        _echo(format_concept_results_table(results))
        _report_config_validation()
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def _cmd_ask(args: argparse.Namespace) -> int:
    from obsitocin.mcp_server import ask_wiki

    result = ask_wiki(
        question=args.question,
        project=getattr(args, "project", None),
        save_to_wiki=getattr(args, "save", False),
    )
    _echo(f"\n{result['answer']}\n")
    if result["sources"]:
        _echo(f"출처: {', '.join(result['sources'])}")
    if result.get("saved"):
        _echo(f"위키에 저장됨: {result['saved_path']}")
    _report_config_validation()
    return 0


def _cmd_embed(_: argparse.Namespace) -> int:
    from obsitocin.embeddings import (
        build_embeddings_for_qas,
        is_configured,
        start_embed_server,
        stop_embed_server,
    )

    if not is_configured():
        print(
            "Error: embedding model not configured. Set OBS_EMBED_MODEL_PATH or place a Qwen3-Embedding GGUF under ~/.local/share/obsitocin/models/.",
            file=sys.stderr,
        )
        return 1

    qa_files = []
    for filepath in sorted(PROCESSED_DIR.glob("*.json")):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if qa.get("status") in ("processed", "written"):
            qa_files.append((filepath.stem, qa))

    if not qa_files:
        _echo("No processed Q&A pairs found.")
        return 0

    _echo(f"Found {len(qa_files)} Q&A pairs to index.")
    try:
        try:
            start_embed_server()
            count = build_embeddings_for_qas(qa_files)
            _echo(f"Generated {count} semantic embedding vectors.")

            try:
                from obsitocin.config import OBS_DIR
                from obsitocin.embeddings import embed_topic_notes

                if OBS_DIR is not None and OBS_DIR.exists():
                    topic_count = embed_topic_notes(OBS_DIR)
                    if topic_count > 0:
                        _echo(f"Generated {topic_count} topic note embedding vectors.")
            except Exception as topic_error:
                _echo(
                    f"Warning: Topic note embedding failed (non-fatal): {topic_error}"
                )
        finally:
            stop_embed_server()
        _report_config_validation()
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def _cmd_organize(args: argparse.Namespace) -> int:
    from obsitocin.organizer import execute_organize, plan_organize

    min_importance = getattr(args, "min_importance", 3)

    if getattr(args, "dry_run", False):
        plan = plan_organize(min_importance=min_importance)
        _echo(f"Min importance threshold: {plan['min_importance']}")
        _echo(f"Total QAs: {plan['total_qas']}")
        _echo(f"  Keep (importance >= {min_importance}): {plan['keep']}")
        _echo(f"  Archive (importance < {min_importance}): {plan['archive']}")
        _echo(f"  Skip (duplicate/skipped/filtered): {plan['skip']}")
        _echo()
        if plan["archivable"]:
            _echo("QAs to archive:")
            for item in plan["archivable"]:
                _echo(
                    f"  - [{item['importance']}/5] {item['title']} "
                    f"({item['project']}, {item['category']})"
                )
        else:
            _echo("No QAs to archive.")
        _echo()
        _echo(f"Projects after rebuild: {', '.join(plan['kept_projects'])}")
        _echo(f"Topics after rebuild: ~{plan['kept_topics']}")
        _report_config_validation()
        return 0

    result = execute_organize(min_importance=min_importance)
    _echo(
        f"Kept {result['kept_qas']} QA(s), archived {result['archived_qas']}, skipped {result['skipped_qas']}"
    )
    _echo(f"Rebuilt {result['topic_writes']} topic note(s)")
    _report_config_validation()
    return 0


def _cmd_uninstall(_: argparse.Namespace) -> int:
    from obsitocin.hooks import unregister_hooks

    if unregister_hooks():
        _echo("Claude Code hooks removed.")
    else:
        _echo("No hooks to remove.")

    _uninstall_skill()
    _echo("vault-search skill removed.")

    if CONFIG_FILE.exists() and _confirm("Remove config file?"):
        CONFIG_FILE.unlink()
        _echo(f"Removed: {CONFIG_FILE}")
        try:
            CONFIG_DIR.rmdir()
        except OSError:
            pass

    _echo()
    _echo("Hooks unregistered. Q&A data in ~/.local/share/obsitocin/ is preserved.")
    _echo("Delete it manually if you want to remove all data.")
    _report_config_validation()
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    from obsitocin.config import OBS_DIR
    from obsitocin.lint import run_all_checks

    if OBS_DIR is None:
        print(
            "Error: Vault not configured. Run 'obsitocin init --vault-dir <path>'",
            file=sys.stderr,
        )
        return 1

    min_knowledge = getattr(args, "min_knowledge", 2)
    result = run_all_checks(OBS_DIR, min_knowledge=min_knowledge)

    if getattr(args, "json_output", False):
        import json

        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["clean"]:
            _echo("✓ Vault is clean. No issues found.")
        else:
            _echo(f"Found {result['total_issues']} issue(s):\n")
            for check_name, issues in result["checks"].items():
                if issues:
                    _echo(f"{check_name}: {len(issues)} issue(s)")
                    for issue in issues[:5]:
                        _echo(f"  - {issue['message']}")
                    if len(issues) > 5:
                        _echo(f"  ... and {len(issues) - 5} more")

    _report_config_validation()
    return 0 if result["clean"] else 1


def _cmd_ingest(args: argparse.Namespace) -> int:
    ingest_source = import_module("obsitocin.ingest").ingest_source

    result = ingest_source(
        source=args.source,
        project=getattr(args, "project", None),
        title=getattr(args, "title", None),
    )
    if result.get("success"):
        _echo(f"Ingested: {result.get('source_page', 'unknown')}")
        _echo(f"Topics updated: {result.get('topics_updated', 0)}")
        if result.get("raw_path"):
            _echo(f"Raw saved: {result['raw_path']}")
    else:
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        return 1
    _report_config_validation()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        __import__("fastmcp")
    except ImportError:
        print(
            "Error: fastmcp is required to run the MCP server.\n"
            "Install it with: pip install 'obsitocin[mcp]'",
            file=sys.stderr,
        )
        return 1

    try:
        from obsitocin.mcp_server import create_server
    except ImportError:
        print("Error: MCP server module not yet initialized.", file=sys.stderr)
        return 1

    server = create_server()
    server.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obsitocin")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser(
        "init",
        help="Initialize config, create directories, and register Claude Code hooks.",
    )
    init_parser.add_argument("--vault-dir", required=True)
    init_parser.add_argument(
        "--llm-provider",
        choices=["codex", "claude", "gemini", "qwen"],
        help="Default LLM provider for tagging (default: claude).",
    )
    init_parser.set_defaults(handler=_cmd_init)

    run_parser = subparsers.add_parser("run", help="Run the pipeline.")
    run_parser.add_argument(
        "--llm-provider",
        choices=["codex", "claude", "gemini", "qwen"],
        help="Override LLM provider for this run.",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview which pending files would be processed without changing files.",
    )
    run_parser.add_argument(
        "--detect-pii",
        action="store_true",
        default=None,
        help="Enable regex-based PII detection for this run.",
    )
    run_parser.add_argument(
        "--redact-pii",
        action="store_true",
        default=None,
        help="Redact detected PII before metadata extraction and storage.",
    )
    run_parser.add_argument(
        "--skip-sensitive",
        action="store_true",
        default=None,
        help="Skip entries whose PII risk meets the configured threshold.",
    )
    run_parser.set_defaults(handler=_cmd_run)

    status_parser = subparsers.add_parser(
        "status", help="Show counts of pending, processed, and written Q&A pairs."
    )
    status_parser.set_defaults(handler=_cmd_status)

    verify_parser = subparsers.add_parser(
        "verify",
        help="Check queue, processed files, and embeddings index consistency.",
    )
    verify_parser.set_defaults(handler=_cmd_verify)

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Remove orphan prompt files and stale embeddings index entries.",
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview cleanup actions without modifying files.",
    )
    cleanup_parser.set_defaults(handler=_cmd_cleanup)

    query_parser = subparsers.add_parser(
        "query", help="Search across accumulated knowledge."
    )
    query_parser.add_argument("query_text")
    query_parser.add_argument("--top-k", "-k", type=int, default=5)
    query_parser.add_argument(
        "--type", dest="memory_type", choices=["static", "dynamic"]
    )
    query_parser.add_argument(
        "--category",
        choices=[
            "development",
            "debugging",
            "architecture",
            "devops",
            "data",
            "testing",
            "tooling",
            "policy",
            "domain",
            "other",
        ],
    )
    query_parser.add_argument("--importance-min", type=int)
    query_parser.add_argument("--context", dest="context_mode", action="store_true")
    query_parser.set_defaults(handler=_cmd_query)

    concepts_parser = subparsers.add_parser(
        "concepts", help="Search canonical concepts across accumulated knowledge."
    )
    concepts_parser.add_argument("query_text")
    concepts_parser.add_argument("--top-k", "-k", type=int, default=5)
    concepts_parser.set_defaults(handler=_cmd_concepts)

    ask_parser = subparsers.add_parser(
        "ask",
        help="Ask a question against the wiki and get an answer with citations.",
    )
    ask_parser.add_argument("question", help="Question to ask.")
    ask_parser.add_argument("--project", help="Limit search to this project.")
    ask_parser.add_argument(
        "--save",
        action="store_true",
        help="Save the answer to the wiki.",
    )
    ask_parser.set_defaults(handler=_cmd_ask)

    embed_parser = subparsers.add_parser(
        "embed", help="Generate semantic embedding vectors for processed Q&A pairs."
    )
    embed_parser.set_defaults(handler=_cmd_embed)

    organize_parser = subparsers.add_parser(
        "organize",
        help="Curate vault: archive low-importance sessions, rebuild concept notes.",
    )
    organize_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview organize actions without modifying files.",
    )
    organize_parser.add_argument(
        "--min-importance",
        type=int,
        default=3,
        choices=range(1, 6),
        metavar="{1-5}",
        help="Minimum importance to keep (default: 3). Sessions below this are archived.",
    )
    organize_parser.set_defaults(handler=_cmd_organize)

    lint_parser = subparsers.add_parser(
        "lint",
        help="Check vault content for issues: broken links, orphan topics, thin notes, MOC consistency.",
    )
    lint_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as JSON.",
    )
    lint_parser.add_argument(
        "--min-knowledge",
        type=int,
        default=2,
        metavar="{1-10}",
        help="Minimum knowledge items for thin note check (default: 2).",
    )
    lint_parser.set_defaults(handler=_cmd_lint)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest external source (URL or file) into the vault.",
    )
    ingest_parser.add_argument("source", help="URL or local file path to ingest.")
    ingest_parser.add_argument(
        "--project",
        help="Target project name (default: current directory name).",
    )
    ingest_parser.add_argument("--title", help="Override source title.")
    ingest_parser.set_defaults(handler=_cmd_ingest)

    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Remove Claude Code hooks and optional local config."
    )
    uninstall_parser.set_defaults(handler=_cmd_uninstall)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the obsitocin MCP server (requires fastmcp: pip install obsitocin[mcp]).",
    )
    serve_parser.set_defaults(handler=_cmd_serve)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return
    raise SystemExit(handler(args))


if __name__ == "__main__":
    main()
