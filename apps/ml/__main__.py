"""Train and validate the scorecard: python -m apps.ml --seed 42 --n 5000 --version scorecard_v1"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from apps.world import generate_world
from apps.world.generator import DEFAULT_AS_OF, DEFAULT_N, DEFAULT_SEED

from .train import TrainingResult, train_scorecard
from .validate import validate_scorecard
from .validation import ValidationReport

DEFAULT_OUT = Path(__file__).parent


def write_result(result: TrainingResult, validation: ValidationReport, out: Path) -> tuple[Path, Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    version = result.scorecard.version
    files = (out / f'{version}.json', out / f'{version}.training.json', out / f'{version}.validation.json')
    for path, model in zip(files, (result.scorecard, result.report, validation)):
        path.write_text(model.model_dump_json(indent=2) + '\n', encoding='utf-8')
    return files


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m apps.ml',
                                     description='Train and validate the scorecard on the synthetic world.')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--n', type=int, default=DEFAULT_N, help='number of companies')
    parser.add_argument('--as-of', type=date.fromisoformat, default=DEFAULT_AS_OF, help='YYYY-MM-DD')
    parser.add_argument('--version', default='scorecard_v1')
    parser.add_argument('--created-at', type=date.fromisoformat, default=date.today(), help='YYYY-MM-DD')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    world = generate_world(args.seed, args.n, args.as_of)
    result = train_scorecard(world, version=args.version, created_at=args.created_at)
    validation = validate_scorecard(world, result.scorecard)
    files = write_result(result, validation, args.out)
    selected = [f.name for f in result.report.features if f.selected]
    print(f'{len(selected)} features, Gini on the training sample {result.report.gini_train:.3f}, '
          f'on the holdout {validation.gini.holdout:.3f}')
    print('\n'.join(f'→ {f}' for f in files))


if __name__ == '__main__':
    main()
