"""Train the scorecard: python -m apps.ml --seed 42 --n 5000 --version scorecard_v1"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from apps.world import generate_world
from apps.world.generator import DEFAULT_AS_OF, DEFAULT_N, DEFAULT_SEED

from .train import TrainingResult, train_scorecard

DEFAULT_OUT = Path(__file__).parent


def write_result(result: TrainingResult, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    scorecard = out / f'{result.scorecard.version}.json'
    report = out / f'{result.scorecard.version}.training.json'
    scorecard.write_text(result.scorecard.model_dump_json(indent=2) + '\n', encoding='utf-8')
    report.write_text(result.report.model_dump_json(indent=2) + '\n', encoding='utf-8')
    return scorecard, report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m apps.ml', description='Train the scorecard on the synthetic world.')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--n', type=int, default=DEFAULT_N, help='number of companies')
    parser.add_argument('--as-of', type=date.fromisoformat, default=DEFAULT_AS_OF, help='YYYY-MM-DD')
    parser.add_argument('--version', default='scorecard_v1')
    parser.add_argument('--created-at', type=date.fromisoformat, default=date.today(), help='YYYY-MM-DD')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    result = train_scorecard(generate_world(args.seed, args.n, args.as_of), version=args.version,
                             created_at=args.created_at)
    scorecard, report = write_result(result, args.out)
    selected = [f.name for f in result.report.features if f.selected]
    print(f'{len(selected)} features, Gini on the training sample {result.report.gini_train:.3f}')
    print(f'→ {scorecard}\n→ {report}')


if __name__ == '__main__':
    main()
