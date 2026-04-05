#!/usr/bin/env python3
"""Vault organizer for the project > topic structure.

Clears the vault and rebuilds topic notes from kept QAs only.
Raw data (processed/ JSON) is NEVER modified.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from obsitocin.config import LOGS_DIR, OBS_DIR, PROCESSED_DIR

LOG_FILE = LOGS_DIR / "organizer.log" if LOGS_DIR else Path("organizer.log")
DEFAULT_MIN_IMPORTANCE = 3


def log(msg: str) -> None:
    if LOGS_DIR:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_all_qas() -> list[tuple[Path, dict]]:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    records: list[tuple[Path, dict]] = []
    for filepath in sorted(PROCESSED_DIR.glob("*.json")):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception):
            continue
        if qa.get("status") in ("processed", "written"):
            records.append((filepath, qa))
    return records


def classify_qas(
    records: list[tuple[Path, dict]],
    min_importance: int = DEFAULT_MIN_IMPORTANCE,
) -> dict[str, list[tuple[Path, dict]]]:
    keep: list[tuple[Path, dict]] = []
    archive: list[tuple[Path, dict]] = []
    skip: list[tuple[Path, dict]] = []

    for filepath, qa in records:
        status = qa.get("status", "")
        if status in ("duplicate", "skipped", "filtered"):
            skip.append((filepath, qa))
            continue
        tagging = qa.get("tagging_result", {})
        importance = tagging.get("importance", 3)
        if importance >= min_importance:
            keep.append((filepath, qa))
        else:
            archive.append((filepath, qa))

    return {"keep": keep, "archive": archive, "skip": skip}


def _clear_vault() -> None:
    if OBS_DIR is None:
        return
    for sub in ("projects", "daily"):
        target = OBS_DIR / sub
        if target.exists():
            shutil.rmtree(target)
    moc = OBS_DIR / "_MOC.md"
    if moc.exists():
        moc.unlink()


def plan_organize(min_importance: int = DEFAULT_MIN_IMPORTANCE) -> dict:
    records = load_all_qas()
    classified = classify_qas(records, min_importance)

    archivable: list[dict] = []
    for _fp, qa in classified["archive"]:
        tagging = qa.get("tagging_result", {})
        archivable.append(
            {
                "title": tagging.get("title", "Untitled"),
                "importance": tagging.get("importance", 3),
                "category": tagging.get("category", "other"),
                "project": Path(qa.get("cwd", "")).name or "uncategorized",
            }
        )

    kept_topics: set[str] = set()
    kept_projects: set[str] = set()
    for _fp, qa in classified["keep"]:
        tagging = qa.get("tagging_result", {})
        for t in tagging.get("topics") or tagging.get("key_concepts", []):
            kept_topics.add(t)
        kept_projects.add(Path(qa.get("cwd", "")).name or "uncategorized")

    return {
        "total_qas": len(records),
        "keep": len(classified["keep"]),
        "archive": len(classified["archive"]),
        "skip": len(classified["skip"]),
        "archivable": archivable,
        "kept_topics": len(kept_topics),
        "kept_projects": sorted(kept_projects),
        "min_importance": min_importance,
    }


def execute_organize(min_importance: int = DEFAULT_MIN_IMPORTANCE) -> dict:
    if OBS_DIR is None:
        raise RuntimeError("Vault directory not configured.")

    log("=" * 50)
    log(f"Organize started (min_importance={min_importance})")

    records = load_all_qas()
    classified = classify_qas(records, min_importance)
    log(
        f"Classified: {len(classified['keep'])} keep, "
        f"{len(classified['archive'])} archive, "
        f"{len(classified['skip'])} skip"
    )

    _clear_vault()
    log("Cleared vault for rebuild")

    from obsitocin.topic_writer import write_notes_for_qa

    kept_qas = [qa for _, qa in classified["keep"]]
    topics_total = 0
    for qa in kept_qas:
        result = write_notes_for_qa(qa)
        topics_total += result.get("topics_written", 0)

    log(f"Rebuilt: {len(kept_qas)} QAs → {topics_total} topic writes")
    log("Organize finished")

    return {
        "kept_qas": len(kept_qas),
        "archived_qas": len(classified["archive"]),
        "skipped_qas": len(classified["skip"]),
        "topic_writes": topics_total,
    }

    # 1. Build concept catalog & canonicalize
    concept_catalog = build_concept_catalog(kept_qas)
    alias_to_canonical = concept_catalog["alias_to_canonical"]

    for qa in kept_qas:
        tagging = qa.setdefault("tagging_result", {})
        canonical = canonicalize_concepts(
            tagging.get("key_concepts", []), alias_to_canonical
        )
        tagging["canonical_concepts"] = canonical

    # 2. Accumulate daily entries + concept refs
    daily_entries: dict[str, list[str]] = defaultdict(list)
    daily_concepts: dict[str, list[str]] = defaultdict(list)
    concept_refs: dict[str, list[dict]] = defaultdict(list)

    for qa in kept_qas:
        ts = qa.get("timestamp", "")
        try:
            date_str = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_str = datetime.now().strftime("%Y-%m-%d")

        daily_entries[date_str].append(build_daily_entry(qa))
        for concept in _concepts_for(qa):
            concept_refs[concept].append(qa)
            daily_concepts[date_str].append(concept)

    # 3. Session relations
    session_rels = build_session_relations(kept_qas)

    # 4. Write session notes
    written_sessions: list[str] = []
    for qa in kept_qas:
        session_key = str(qa.get("session_id", session_filename(qa)))
        see_also = session_rels.get(session_key, [])
        write_session_note(qa, see_also)
        written_sessions.append(session_relative_path(qa))

    # 5. Write threads
    threads = group_issue_threads(kept_qas)
    for idx, thread in enumerate(threads):
        project = _extract_project_name(thread[0].get("cwd", ""))
        write_issue_thread(thread, idx, project)

    # 6. Write daily notes
    written_dailies: list[str] = []
    for date_str, entries in daily_entries.items():
        write_daily_note(date_str, entries, daily_concepts.get(date_str))
        written_dailies.append(date_str)

    # 7. Write concept notes (preserves User Notes via existing logic)
    relations = classify_concept_relations(concept_refs)
    written_concepts: list[tuple[str, str] | str] = []
    concept_stems: set[str] = set()
    for concept, refs in concept_refs.items():
        related = relations.get(concept)
        aliases = concept_catalog["concepts"].get(concept, {}).get("aliases", [concept])
        from obsitocin.concepts import concept_note_stem

        write_concept_note(concept, refs, aliases, related)
        written_concepts.append((concept_relative_path(concept), concept))
        concept_stems.add(concept_note_stem(concept))

    # 8. Update MOC
    if written_dailies or written_concepts or written_sessions:
        update_moc(written_dailies, written_concepts, written_sessions)

    # 9. Update profile
    try:
        write_user_profile(kept_qas, concept_refs)
    except Exception as e:
        log(f"Profile generation failed: {e}")

    return {
        "sessions": len(written_sessions),
        "dailies": len(written_dailies),
        "concepts": len(written_concepts),
        "threads": len(threads),
        "concept_stems": concept_stems,
        "daily_dates": set(written_dailies),
    }


# ── Prune orphans ──


def prune_orphan_concepts(valid_stems: set[str]) -> list[str]:
    """Remove concept .md files not in *valid_stems*.

    Only removes files that contain the auto-generated marker
    ``note_id: concept-`` to avoid deleting manually created notes.
    """
    pruned: list[str] = []
    if not CONCEPTS_DIR or not CONCEPTS_DIR.exists():
        return pruned
    for f in sorted(CONCEPTS_DIR.glob("*.md")):
        if f.stem in valid_stems:
            continue
        try:
            content = f.read_text(errors="replace")
        except OSError:
            continue
        if "note_id: concept-" in content:
            f.unlink()
            pruned.append(f.name)
    return pruned


def prune_orphan_dailies(valid_dates: set[str]) -> list[str]:
    """Remove daily .md files for dates not in *valid_dates*."""
    pruned: list[str] = []
    if not DAILY_DIR or not DAILY_DIR.exists():
        return pruned
    for f in sorted(DAILY_DIR.glob("*.md")):
        date_part = f.stem
        if date_part in valid_dates:
            continue
        try:
            content = f.read_text(errors="replace")
        except OSError:
            continue
        if "type: daily-ai-log" in content:
            f.unlink()
            pruned.append(f.name)
    return pruned


# ── Public API ──


def plan_organize(min_importance: int = DEFAULT_MIN_IMPORTANCE) -> dict:
    """Preview what ``organize`` would do without making changes.

    Returns a summary dict suitable for CLI display.
    """
    records = load_all_qas()
    classified = classify_qas(records, min_importance)

    archivable: list[dict] = []
    for _filepath, qa in classified["archive"]:
        tagging = qa.get("tagging_result", {})
        note_path = _find_session_note(qa)
        archivable.append(
            {
                "title": tagging.get("title", "Untitled"),
                "importance": tagging.get("importance", 3),
                "category": tagging.get("category", "other"),
                "note_exists": note_path is not None,
            }
        )

    kept_concepts: set[str] = set()
    for _filepath, qa in classified["keep"]:
        tagging = qa.get("tagging_result", {})
        for concept in tagging.get("key_concepts", []):
            kept_concepts.add(concept)

    current_concept_count = 0
    if CONCEPTS_DIR and CONCEPTS_DIR.exists():
        current_concept_count = len(list(CONCEPTS_DIR.glob("*.md")))

    return {
        "total_qas": len(records),
        "keep": len(classified["keep"]),
        "archive": len(classified["archive"]),
        "skip": len(classified["skip"]),
        "archivable_notes": archivable,
        "kept_concepts": len(kept_concepts),
        "current_concept_notes": current_concept_count,
        "min_importance": min_importance,
    }


def execute_organize(min_importance: int = DEFAULT_MIN_IMPORTANCE) -> dict:
    """Execute the organize operation.

    1. Remove low-importance session notes from vault.
    2. Remove stale thread files.
    3. Rebuild concept notes, daily notes, MOC, profile from kept QAs.
    4. Prune orphan concept and daily notes.
    5. Clean up empty directories.

    Raw processed JSON is **never** modified.
    """
    if OBS_DIR is None:
        raise RuntimeError(
            "Vault directory not configured. Run 'obsitocin init' first."
        )

    log("=" * 50)
    log(f"Organize started (min_importance={min_importance})")

    records = load_all_qas()
    classified = classify_qas(records, min_importance)
    log(
        f"Classified: {len(classified['keep'])} keep, "
        f"{len(classified['archive'])} archive, "
        f"{len(classified['skip'])} skip"
    )

    removed_sessions: list[str] = []
    for _filepath, qa in classified["archive"]:
        note_path = _find_session_note(qa)
        if note_path and note_path.exists():
            note_path.unlink()
            removed_sessions.append(note_path.name)
            log(f"Removed session note: {note_path.name}")

    removed_threads = 0
    for thread_path in _find_thread_files():
        thread_path.unlink()
        removed_threads += 1
    if removed_threads:
        log(f"Removed {removed_threads} thread file(s)")

    kept_qas = [qa for _, qa in classified["keep"]]
    rebuild = rebuild_vault_notes(kept_qas)
    log(
        f"Rebuilt: {rebuild['sessions']} sessions, "
        f"{rebuild['concepts']} concepts, "
        f"{rebuild['dailies']} dailies, "
        f"{rebuild['threads']} threads"
    )

    pruned_concepts = prune_orphan_concepts(rebuild["concept_stems"])
    for name in pruned_concepts:
        log(f"Pruned orphan concept: {name}")

    pruned_dailies = prune_orphan_dailies(rebuild["daily_dates"])
    for name in pruned_dailies:
        log(f"Pruned orphan daily: {name}")

    if PARA_PROJECTS_DIR:
        _clean_empty_dirs(PARA_PROJECTS_DIR)

    log("Organize finished")

    return {
        "removed_sessions": len(removed_sessions),
        "removed_threads": removed_threads,
        "kept_sessions": rebuild["sessions"],
        "rebuilt_concepts": rebuild["concepts"],
        "pruned_concepts": len(pruned_concepts),
        "rebuilt_dailies": rebuild["dailies"],
        "pruned_dailies": len(pruned_dailies),
        "rebuilt_threads": rebuild["threads"],
    }
