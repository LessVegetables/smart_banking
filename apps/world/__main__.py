"""Generate the synthetic world: python -m apps.world --seed 42 --n 5000 --out apps/world/out"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .generator import DEFAULT_AS_OF, DEFAULT_N, DEFAULT_SEED, World, generate_world

DEFAULT_OUT = Path(__file__).parent / 'out'


def write_world(world: World, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / 'meta.json').write_text(world.meta.model_dump_json(indent=2) + '\n', encoding='utf-8')
    with (out / 'companies.jsonl').open('w', encoding='utf-8', newline='\n') as f:
        for company in world.companies:
            f.write(company.model_dump_json() + '\n')


def read_world(out: Path) -> World:
    meta = json.loads((out / 'meta.json').read_text(encoding='utf-8'))
    lines = (out / 'companies.jsonl').read_text(encoding='utf-8').splitlines()
    return World.model_validate({'meta': meta, 'companies': [json.loads(line) for line in lines]})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m apps.world', description='Generate the synthetic world.')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--n', type=int, default=DEFAULT_N, help='number of companies')
    parser.add_argument('--as-of', type=date.fromisoformat, default=DEFAULT_AS_OF, help='YYYY-MM-DD')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    world = generate_world(args.seed, args.n, args.as_of)
    write_world(world, args.out)
    print(f'{len(world.companies)} companies → {args.out}')


if __name__ == '__main__':
    main()
