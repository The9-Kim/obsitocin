"""Shared test helpers for obsitocin tests."""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path


DOCKER_TOPIC_CONTENT = """\
---
title: Docker
project: test-project
tags:
  - docker
  - devops
  - container
type: topic-note
created: 2026-04-05
updated: 2026-04-05
sessions: 2
importance: 4
---

# Docker

## 핵심 지식

- Docker는 컨테이너 기반 가상화 기술로 프로세스 격리를 제공한다
- docker build -t image-name . 명령으로 이미지를 빌드한다
- docker run -d -p 8080:80 image-name 으로 컨테이너를 실행한다

## 히스토리

- 2026-04-05 10:00: Docker 컨테이너 설정 및 배포 파이프라인 구성
- 2026-04-04 15:30: Docker Compose로 멀티 컨테이너 환경 구성

## User Notes

<!-- OBSITOCIN:BEGIN USER NOTES -->
여기에 직접 정리한 내용을 작성하세요.
<!-- OBSITOCIN:END USER NOTES -->
"""

PYTHON_VENV_TOPIC_CONTENT = """\
---
title: Python 가상환경 (venv)
project: test-project
tags:
  - python
  - venv
  - development
type: topic-note
created: 2026-04-05
updated: 2026-04-05
sessions: 1
importance: 3
---

# Python 가상환경 (venv)

## 핵심 지식

- python -m venv .venv 로 가상환경 생성
- source .venv/bin/activate 로 활성화

## 히스토리

- 2026-04-05 09:00: obsitocin 프로젝트 전용 venv 설정

## User Notes

<!-- OBSITOCIN:BEGIN USER NOTES -->
여기에 직접 정리한 내용을 작성하세요.
<!-- OBSITOCIN:END USER NOTES -->
"""


def create_test_vault(tmp_dir: str) -> str:
    """Create a synthetic Obsidian vault for testing.

    Args:
        tmp_dir: Temporary directory path where vault will be created.

    Returns:
        The vault root path (tmp_dir/obsitocin/).
    """
    vault_root = Path(tmp_dir) / "obsitocin"

    # Create directory structure
    topics_dir = vault_root / "projects" / "test-project" / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    daily_dir = vault_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    (vault_root / "raw" / "sessions").mkdir(parents=True, exist_ok=True)

    # Topic notes
    (topics_dir / "Docker.md").write_text(DOCKER_TOPIC_CONTENT)
    (topics_dir / "Python-가상환경-venv.md").write_text(PYTHON_VENV_TOPIC_CONTENT)

    # Project index
    index_content = """\
---
title: test-project
type: project-index
updated: 2026-04-05
topics: 2
---

# test-project

## 주제

- [[projects/test-project/topics/Docker|Docker]] (2)
- [[projects/test-project/topics/Python-가상환경-venv|Python 가상환경 (venv)]] (1)
"""
    (vault_root / "projects" / "test-project" / "_index.md").write_text(index_content)

    # MOC
    moc_content = """\
---
title: Knowledge Base
updated: 2026-04-05
type: moc
---

# Knowledge Base

## 프로젝트

### [[projects/test-project/_index|test-project]]

  - [[projects/test-project/topics/Docker|Docker]] (2)
  - [[projects/test-project/topics/Python-가상환경-venv|Python 가상환경 (venv)]] (1)

## 작업 로그

- [[daily/2026-04-05|2026-04-05]]
"""
    (vault_root / "_MOC.md").write_text(moc_content)

    # Daily log
    daily_content = """\
---
title: "2026-04-05 작업 로그"
date: 2026-04-05
type: work-log
---

# 2026-04-05 작업 로그

- 10:00 [test-project] Docker 컨테이너 설정 및 배포 파이프라인 구성 → [[projects/test-project/topics/Docker|Docker]]
- 09:00 [test-project] obsitocin 프로젝트 전용 venv 설정 → [[projects/test-project/topics/Python-가상환경-venv|Python 가상환경 (venv)]]
"""
    (daily_dir / "2026-04-05.md").write_text(daily_content)

    return str(vault_root)


def make_legacy_queue_item(**overrides) -> dict:
    """Create a legacy queue item (no source_type field).

    Args:
        **overrides: Fields to override in the default item.

    Returns:
        A dictionary representing a legacy queue item.
    """
    item = {
        "session_id": "test-session-123",
        "timestamp": "2026-04-05T10:00:00",
        "cwd": "/tmp/test-project",
        "prompt": "Docker 컨테이너를 어떻게 빌드하나요?",
        "response": "docker build -t image-name . 명령을 사용합니다.",
        "content_hash": "abc123def456abcd",
        "status": "pending",
        "transcript_path": "",
    }
    item.update(overrides)
    return item


def make_new_queue_item(**overrides) -> dict:
    """Create a new-format queue item with source_type field.

    Args:
        **overrides: Fields to override in the default item.

    Returns:
        A dictionary representing a new-format queue item.
    """
    item = {
        "session_id": "test-session-456",
        "timestamp": "2026-04-05T11:00:00",
        "cwd": "/tmp/test-project",
        "prompt": "Python 가상환경을 어떻게 만드나요?",
        "response": "python -m venv .venv 명령을 사용합니다.",
        "content_hash": "xyz789abc123xyz7",
        "status": "pending",
        "transcript_path": "",
        "source_type": "claude_code",
        "source_metadata": {
            "session_id": "test-session-456",
            "transcript_path": "",
        },
    }
    item.update(overrides)
    return item


def make_processed_qa(**overrides) -> dict:
    """Create a processed QA item with tagging_result.

    Args:
        **overrides: Fields to override in the default item.

    Returns:
        A dictionary representing a processed QA item.
    """
    item = {
        "session_id": "test-session-789",
        "timestamp": "2026-04-05T09:00:00",
        "cwd": "/tmp/test-project",
        "prompt": "Docker란 무엇인가요?",
        "response": "Docker는 컨테이너 기반 가상화 플랫폼입니다.",
        "content_hash": "processed123hash",
        "status": "processed",
        "source_type": "claude_code",
        "tagging_result": {
            "title": "Docker 기초 개념",
            "should_store": True,
            "topics": [
                {
                    "name": "Docker",
                    "knowledge": [
                        "Docker는 컨테이너 기반 가상화 기술",
                        "docker build -t image-name . 로 이미지 빌드",
                    ],
                }
            ],
            "work_summary": "Docker 기초 개념 학습",
            "tags": ["docker", "devops", "container"],
            "category": "devops",
            "importance": 4,
            "memory_type": "static",
            "key_concepts": ["Docker"],
            "distilled_knowledge": [
                "Docker는 컨테이너 기반 가상화 기술",
                "docker build -t image-name . 로 이미지 빌드",
            ],
        },
    }
    item.update(overrides)
    return item


if __name__ == "__main__":
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmp:
        vault = create_test_vault(tmp)
        print(f"Vault: {vault}")
        print(f"Vault exists: {os.path.isdir(vault)}")

        topics_dir = os.path.join(vault, "projects", "test-project", "topics")
        print(f"Topics: {os.listdir(topics_dir)}")

        moc_path = os.path.join(vault, "_MOC.md")
        print(f"MOC exists: {os.path.isfile(moc_path)}")

        daily_path = os.path.join(vault, "daily", "2026-04-05.md")
        print(f"Daily log exists: {os.path.isfile(daily_path)}")
