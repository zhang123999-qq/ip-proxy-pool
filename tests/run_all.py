"""
一键运行所有测试
=================

运行：python tests/run_all.py
"""
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

test_files = [
    "test_scorer.py",
    "test_api.py",
    "test_crawler.py",
    "test_full_flow.py",
    "test_smart_crawl.py",
    "test_storage.py",
    "test_admin_auth.py",
    "test_custom_sources.py",
    "test_validator_stages.py",
]


def main():
    failed = []
    for t in test_files:
        print(f"\n{'='*60}")
        print(f"  运行: {t}")
        print('='*60)
        result = subprocess.run(
            [sys.executable, str(TESTS_DIR / t)],
            cwd=str(TESTS_DIR.parent),
        )
        if result.returncode != 0:
            failed.append(t)

    print(f"\n{'='*60}")
    if failed:
        print(f"  ❌ {len(failed)} 个测试套件失败: {failed}")
        sys.exit(1)
    else:
        print(f"  ✅ 全部 {len(test_files)} 个测试套件通过")
        print('='*60)


if __name__ == "__main__":
    main()
