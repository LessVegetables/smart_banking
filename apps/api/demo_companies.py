"""Public, synthetic company choices for the shared demo client profile."""
from __future__ import annotations

from pathlib import Path
from apps.world.generator import World, generate_world
from apps.world.personas import PERSONAS_FILE, read as read_personas


def company_options(world: World, personas_path: Path = PERSONAS_FILE) -> list[dict[str, str]]:
    """Use current T12 personas, with a deterministic synthetic fallback so the catalog is never empty."""
    by_id = {company.company_id: company for company in world.companies}
    options: list[dict[str, str]] = []
    try:
        persona_file = read_personas(personas_path)
    except (OSError, ValueError):
        persona_file = None

    if persona_file is not None:
        for persona in persona_file.personas:
            company = by_id.get(persona.company_id or '')
            if company is None or company.inn != persona.inn:
                continue
            options.append({
                'persona_id': persona.persona_id,
                'title': persona.title,
                'story': persona.story,
                'inn': company.inn,
                'legal_form': company.legal_form.value,
                'company_name': company.egrul.name,
            })

    if options:
        return options

    # MOCK: when the generated persona catalog is missing, empty or stale, keep the demo usable with
    # the first company from this world. If a damaged world file has no companies, rebuild the default.
    fallback_world = world if world.companies else generate_world()
    company = fallback_world.companies[0]
    return [{
        'persona_id': 'default_company',
        'title': 'Базовая демо-компания',
        'story': 'Синтетическая компания из текущего мира. Доступна как запасной сценарий.',
        'inn': company.inn,
        'legal_form': company.legal_form.value,
        'company_name': company.egrul.name,
    }]


def available_demo_inns(world: World, personas_path: Path = PERSONAS_FILE) -> set[str]:
    return {option['inn'] for option in company_options(world, personas_path)}
