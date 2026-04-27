from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://wiki.leagueoflegends.com"
DEFAULT_SOURCE = Path("ScrapedPages/ItemPageWiki.html")
DEFAULT_IMAGE_DIR = Path("item_images")
DEFAULT_JSON_PATH = Path("items.json")
DEFAULT_FULL_JSON_PATH = Path("itemsALL.json")
DEFAULT_STATS_PATH = Path("stats.txt")
DEFAULT_SUBPAGE_CACHE_DIR = Path("item_subpages")
DEFAULT_CHAMPION_ULT_JSON_PATH = Path("champion_ultimate_cooldowns.json")
DEFAULT_CHAMPION_SUBPAGE_CACHE_DIR = Path("champion_subpages")
DEFAULT_CHAMPION_IMAGE_DIR = Path("champion_icons")
TARGET_GAME_MODE = "classic sr 5v5"
ITEM_TYPES = {"Starter items", "Boots", "Basic items", "Epic items", "Legendary items"}
EXCLUDED_BODY_SECTIONS = {
    "Notes",
    "Map-Specific Differences",
    "Old icons",
    "Media",
    "Trivia",
    "Patch History",
    "Jungle Pet Patch History",
    "References",
    "Other media",
    "Sound Effects",
    "Strategy",
    "Revisions",
    "Background",
    "Interactions",
}

SPECIAL_CASE_STATS = {
    "Sterak's Gage": ["Attack damage"],
    "Endless Hunger": ["Ability haste"]
}

STAT_ALIASES = {
    "ability haste": "Ability haste",
    "ability power": "Ability power",
    "armor": "Armor",
    "armor penetration": "Percentage armor penetration",
    "attack damage": "Attack damage",
    "attack range": "Attack range *",
    "attack speed": "Attack speed",
    "base health regeneration": "Base health regeneration",
    "base mana regeneration": "Base mana regeneration",
    "critical strike chance": "Critical strike chance",
    "critical strike damage": "Critical strike damage",
    "critical strike": "Critical strike chance",
    "gold generation": "Gold generation",
    "heal and shield power": "Heal and shield power",
    "health": "Health",
    "life steal": "Life steal",
    "lifesteal": "Life steal",
    "lethality": "Lethality",
    "magic penetration": "Flat magic penetration",
    "magic resistance": "Magic resistance",
    "mana": "Mana",
    "movement speed": "Flat movement speed",
    "move speed": "Flat movement speed",
    "omnivamp": "Omnivamp",
    "tenacity": "Tenacity",
}


def load_valid_stats(stats_path: Path) -> list[str]:
    return [
        line.strip()
        for line in stats_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def split_csv_field(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def slugify_filename(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "item"


def resolve_url(url: str) -> str:
    return urljoin(BASE_URL, url)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_stat_value(tag: Tag) -> str:
    text = clean_text(tag.get_text(" ", strip=True))
    return re.sub(r"(\d)\.\s+(\d)", r"\1.\2", text)


def get_image_extension(image_url: str) -> str:
    parsed = urlparse(image_url)
    suffix = Path(parsed.path).suffix.lower()
    return suffix if suffix else ".png"


def download_image(image_url: str, item_name: str, image_dir: Path, session: requests.Session) -> Path:
    image_dir.mkdir(parents=True, exist_ok=True)
    image_path = image_dir / f"{slugify_filename(item_name)}{get_image_extension(image_url)}"
    if image_path.exists():
        return image_path

    response = get_with_retries(session, image_url)
    image_path.write_bytes(response.content)
    return image_path


def load_html(source: str | Path, session: requests.Session) -> str:
    source_str = str(source)
    if source_str.startswith(("http://", "https://")):
        response = get_with_retries(session, source_str)
        return response.text
    return Path(source).read_text(encoding="utf-8")


def get_with_retries(session: requests.Session, url: str, *, attempts: int = 5) -> requests.Response:
    for attempt in range(attempts):
        response = session.get(url, timeout=30)
        if response.status_code != 429:
            response.raise_for_status()
            time.sleep(0.25)
            return response

        retry_after = response.headers.get("Retry-After")
        wait_seconds = float(retry_after) if retry_after and retry_after.isdigit() else min(60, 5 * (attempt + 1))
        time.sleep(wait_seconds)

    response.raise_for_status()
    return response


def normalize_stat_name(raw_name: str, row_text: str, valid_stats: set[str]) -> str | None:
    key = raw_name.strip().lower()
    row_key = row_text.strip().lower()
    if "base health regeneration" in row_key:
        return "Base health regeneration"
    if "base mana regeneration" in row_key:
        return "Base mana regeneration"
    if key in {"movement speed", "move speed"}:
        return (
            "Percentage movement speed"
            if "%" in row_text
            else "Flat movement speed"
        )
    if key == "magic penetration":
        return (
            "Percentage magic penetration"
            if "%" in row_text
            else "Flat magic penetration"
        )
    if key == "armor penetration":
        return (
            "Percentage armor penetration"
            if "%" in row_text
            else "Percentage armor penetration"
        )

    mapped = STAT_ALIASES.get(key)
    if mapped in valid_stats:
        return mapped
    return None


def get_stats_tab_container(soup: BeautifulSoup) -> Tag | None:
    infobox = soup.select_one("div.infobox")
    if not infobox:
        return None

    stats_header = infobox.find(
        lambda tag: isinstance(tag, Tag)
        and tag.name == "div"
        and "infobox-header" in (tag.get("class") or [])
        and tag.get_text(" ", strip=True).lower() == "stats"
    )
    if not stats_header:
        return None

    for sibling in stats_header.find_next_siblings():
        classes = sibling.get("class") or []
        if "infobox-header" in classes:
            break
        if "tabber" in classes or "infobox-section-stacked" in classes or "infobox-section" in classes:
            return sibling
    return None


def extract_stat_rows(stats_container: Tag) -> list[Tag]:
    base_tab = stats_container.select_one('.tabbertab[data-title="Base"]')
    search_root = base_tab or stats_container
    return search_root.select("div.infobox-data-row div.infobox-data-value")


def extract_stats_from_subpage(html: str, valid_stats: set[str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    stats_data = extract_infobox_stats_data(soup, valid_stats)
    return stats_data["stats"]


def apply_special_case_stats(item_name: str, stats: list[str], valid_stats: set[str]) -> list[str]:
    merged = list(stats)
    for stat_name in SPECIAL_CASE_STATS.get(item_name, []):
        if stat_name in valid_stats and stat_name not in merged:
            merged.append(stat_name)
    return merged


def get_cached_subpage_html(
    item_url: str,
    session: requests.Session,
    cache_dir: Path,
) -> str:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{slugify_filename(Path(urlparse(item_url).path).name)}.html"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    response = get_with_retries(session, item_url)
    cache_path.write_text(response.text, encoding="utf-8")
    return response.text


def extract_infobox_stats_data(soup: BeautifulSoup, valid_stats: set[str]) -> dict[str, Any]:
    stats_container = get_stats_tab_container(soup)
    if not stats_container:
        return {"stats": [], "tabs": {}}

    tabs: dict[str, list[dict[str, Any]]] = {}
    stats: list[str] = []
    tab_nodes = stats_container.select(".tabbertab")
    if not tab_nodes:
        tab_nodes = [stats_container]

    for tab_node in tab_nodes:
        tab_name = tab_node.get("data-title", "Base") if isinstance(tab_node, Tag) else "Base"
        rows: list[dict[str, Any]] = []
        for value_div in tab_node.select("div.infobox-data-row div.infobox-data-value"):
            row_text = clean_text(value_div.get_text(" ", strip=True))
            stat_link = value_div.find("a", title=True)
            raw_stat_name = stat_link.get("title", "") if stat_link else row_text
            normalized = normalize_stat_name(raw_stat_name, row_text, valid_stats)
            rows.append(
                {
                    "text": row_text,
                    "raw_stat": raw_stat_name,
                    "stat": normalized,
                }
            )
            if normalized and normalized not in stats:
                stats.append(normalized)
        if rows:
            tabs[tab_name] = rows

    return {"stats": stats, "tabs": tabs}


def extract_infobox_image_url(infobox: Tag | None) -> str | None:
    if not infobox:
        return None
    image = infobox.select_one(".infobox-image img")
    src = image.get("src") if image else None
    return resolve_url(src) if src else None


def extract_infobox_sections(soup: BeautifulSoup) -> dict[str, Any]:
    infobox = soup.select_one("div.infobox")
    if not infobox:
        return {}

    sections: dict[str, Any] = {
        "title": clean_text(infobox.select_one(".infobox-title").get_text(" ", strip=True))
        if infobox.select_one(".infobox-title")
        else None,
        "image_url": extract_infobox_image_url(infobox),
    }

    current_header: str | None = None
    for child in infobox.find_all(recursive=False):
        if not isinstance(child, Tag):
            continue

        classes = set(child.get("class") or [])
        if "infobox-header" in classes:
            current_header = clean_text(child.get_text(" ", strip=True))
            continue

        if current_header == "Stats":
            continue

        if not current_header:
            continue

        if "infobox-section" in classes:
            rows = [
                clean_text(value_div.get_text(" ", strip=True))
                for value_div in child.select("div.infobox-data-row div.infobox-data-value")
            ]
            section_data: dict[str, Any] = {"rows": [row for row in rows if row]}
            linked_items = [tag.get("data-item") for tag in child.select("[data-item]") if tag.get("data-item")]
            if linked_items:
                section_data["linked_items"] = linked_items
            sections[current_header] = section_data
        elif "infobox-section-cell" in classes:
            data = {}
            for row in child.select("div.infobox-data-row"):
                label = row.select_one(".infobox-data-label")
                value = row.select_one(".infobox-data-value")
                if label and value:
                    data[clean_text(label.get_text(" ", strip=True))] = clean_text(
                        value.get_text(" ", strip=True)
                    )
            sections[current_header] = data
        elif "infobox-section-cell-row" in classes:
            values = [
                clean_text(value_div.get_text(" ", strip=True))
                for value_div in child.select(".infobox-data-value")
            ]
            sections[current_header] = values

    return sections


def extract_summary(soup: BeautifulSoup) -> str | None:
    root = soup.select_one("#mw-content-text .mw-parser-output")
    if not root:
        return None

    infobox = root.select_one("div.infobox")
    for sibling in infobox.find_next_siblings() if infobox else root.find_all(recursive=False):
        if isinstance(sibling, Tag) and sibling.name == "p":
            text = clean_text(sibling.get_text(" ", strip=True))
            if text:
                return text
    return None


def extract_recipe_table_data(table: Tag) -> dict[str, Any]:
    return {
        "text": clean_text(table.get_text(" ", strip=True)),
        "items": [
            data_item for data_item in [tag.get("data-item") for tag in table.select("[data-item]")]
            if data_item
        ],
    }


def extract_cost_analysis_data(container: Tag) -> dict[str, Any]:
    entries = [clean_text(li.get_text(" ", strip=True)) for li in container.select("li")]
    total_gold_value = None
    gold_efficiency = None
    for entry in entries:
        if "Total Gold Value" in entry:
            match = re.search(r"=\s*([\d. ]+)", entry)
            if match:
                total_gold_value = clean_text(match.group(1))
        if "gold efficient" in entry.lower():
            match = re.search(r"([\d. ]+)\s*%", entry)
            if match:
                gold_efficiency = clean_text(match.group(1)) + "%"
    return {
        "entries": entries,
        "total_gold_value": total_gold_value,
        "gold_efficiency": gold_efficiency,
        "text": clean_text(container.get_text(" ", strip=True)),
    }


def normalize_heading_text(text: str) -> str:
    return clean_text(text.replace("[ edit source ]", ""))


def extract_body_sections(soup: BeautifulSoup) -> dict[str, Any]:
    root = soup.select_one("#mw-content-text .mw-parser-output")
    if not root:
        return {}

    sections: dict[str, Any] = {}
    for heading in root.select(".mw-heading2"):
        heading_text = normalize_heading_text(heading.get_text(" ", strip=True))
        if heading_text in EXCLUDED_BODY_SECTIONS:
            continue

        content: list[Tag] = []
        sibling = heading.find_next_sibling()
        while sibling and not (isinstance(sibling, Tag) and "mw-heading2" in (sibling.get("class") or [])):
            if isinstance(sibling, Tag):
                content.append(sibling)
            sibling = sibling.find_next_sibling()

        if heading_text == "Recipe":
            recipe_data: dict[str, Any] = {}
            table = next((tag for tag in content if tag.name == "table"), None)
            if table:
                recipe_data["table"] = extract_recipe_table_data(table)
            cost_analysis = next((tag.select_one(".mw-collapsible-content") for tag in content if tag.select_one(".mw-collapsible-content")), None)
            if cost_analysis:
                recipe_data["cost_analysis"] = extract_cost_analysis_data(cost_analysis)
            sections[heading_text] = recipe_data
        elif heading_text == "Similar items":
            similar_items = [
                clean_text(link.get_text(" ", strip=True))
                for link in content[0].select("a")
                if clean_text(link.get_text(" ", strip=True))
            ] if content else []
            sections[heading_text] = similar_items
        elif content:
            sections[heading_text] = [
                clean_text(tag.get_text(" ", strip=True))
                for tag in content
                if clean_text(tag.get_text(" ", strip=True))
            ]

    return sections


def extract_page_categories(soup: BeautifulSoup) -> list[str]:
    return [
        clean_text(link.get_text(" ", strip=True))
        for link in soup.select("#mw-normal-catlinks ul li a")
        if clean_text(link.get_text(" ", strip=True))
    ]


def extract_item_page_data(html: str, valid_stats: set[str]) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    stats_data = extract_infobox_stats_data(soup, valid_stats)
    return {
        "page_title": clean_text(soup.title.get_text(" ", strip=True)) if soup.title else None,
        "summary": extract_summary(soup),
        "infobox": {
            **extract_infobox_sections(soup),
            "Stats": stats_data["tabs"],
        },
        "body_sections": extract_body_sections(soup),
        "categories": extract_page_categories(soup),
        "stats": stats_data["stats"],
    }


def parse_item_divs(
    html: str,
    image_dir: Path,
    session: requests.Session,
    valid_stats: set[str],
    subpage_cache_dir: Path,
) -> dict[str, dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: dict[str, dict[str, Any]] = {}
    item_grid = soup.select_one("#item-grid")
    if not item_grid:
        return items

    for dt in item_grid.select("dt"):
        current_type = dt.get_text(" ", strip=True)
        if current_type not in ITEM_TYPES:
            continue

        tlist = dt.parent.find_next_sibling("div", class_="tlist")
        if not tlist:
            continue

        for item_div in tlist.select("div.item-icon[data-item][data-modes]"):
            item_name = item_div.get("data-item", "").strip()
            game_modes = split_csv_field(item_div.get("data-modes"))
            if not item_name or item_name in items or TARGET_GAME_MODE not in game_modes:
                continue

            item_link = item_div.find("a", href=True)
            img_tag = item_div.find("img")
            img_src = img_tag.get("src") if img_tag else None
            if not item_link or not img_src:
                continue

            item_url = resolve_url(item_link["href"])
            image_url = resolve_url(img_src)
            subpage_html = get_cached_subpage_html(item_url, session, subpage_cache_dir)
            page_data = extract_item_page_data(subpage_html, valid_stats)
            stats = page_data["stats"]
            stats = apply_special_case_stats(item_name, stats, valid_stats)
            image_path = download_image(image_url, item_name, image_dir, session)

            items[item_name] = {
                "name": item_name,
                "type": current_type,
                "stats": stats,
                "game_modes": game_modes,
                "item_url": item_url,
                "img_path": image_path,
                "image_url": image_url,
                "summary": page_data["summary"],
                "page_title": page_data["page_title"],
                "infobox": page_data["infobox"],
                "body_sections": page_data["body_sections"],
                "categories": page_data["categories"],
            }

    return items


def save_items_json(items: dict[str, dict[str, Any]], json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    serializable_items = {
        item_name: {
            **item_data,
            "img_path": str(item_data["img_path"]),
        }
        for item_name, item_data in items.items()
    }
    json_path.write_text(
        json.dumps(serializable_items, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def extract_champion_links(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    champions: list[dict[str, Any]] = []
    seen: set[str] = set()

    for cell in soup.select("td[data-sort-value]"):
        champion_icon = cell.select_one("span.champion-icon[data-champion]")
        if not champion_icon:
            continue

        champion_name = champion_icon.get("data-champion", "").strip()
        link = champion_icon.find("a", href=True) or cell.find("a", href=True)
        image = cell.select_one("figure img") or cell.select_one("img")
        image_src = image.get("src") if image else None
        if not champion_name or not link or champion_name in seen:
            continue

        seen.add(champion_name)
        champions.append(
            {
                "name": champion_name,
                "page_url": resolve_url(link["href"]),
                "image_url": resolve_url(image_src) if image_src else None,
            }
        )

    return champions


def get_page_title(soup: BeautifulSoup) -> str | None:
    page_title = soup.select_one(".mw-page-title-main")
    if page_title:
        return clean_text(page_title.get_text(" ", strip=True))
    if soup.title:
        return clean_text(soup.title.get_text(" ", strip=True).split("|")[0])
    return None


def get_ultimate_containers(soup: BeautifulSoup) -> list[Tag]:
    containers: list[Tag] = []
    for skill in soup.select("div.skill"):
        classes = set(skill.get("class") or [])
        if "skill_r" in classes and "abilitytooltip" not in classes:
            containers.append(skill)
    return containers


def extract_ability_stats(ability_container: Tag) -> dict[str, dict[str, str]]:
    stats: dict[str, dict[str, str]] = {}
    for stat in ability_container.select(".ability-info-stats__stat"):
        label_node = stat.select_one(".ability-info-stats__stat-label")
        value_node = stat.select_one(".ability-info-stats__stat-value")
        if not label_node or not value_node:
            continue

        raw_label = clean_text(label_node.get_text(" ", strip=True))
        label = re.sub(r"\s*:\s*$", "", raw_label).upper()
        stats[label] = {
            "label": label,
            "raw_label": raw_label,
            "value": clean_stat_value(value_node),
        }
    return stats


def find_cooldown_stat(stats: dict[str, dict[str, str]]) -> dict[str, str] | None:
    exact_match = stats.get("COOLDOWN")
    if exact_match:
        return exact_match
    return next((stat for label, stat in stats.items() if "COOLDOWN" in label), None)


def extract_ultimate_cooldown_data(
    html: str,
    *,
    champion_name: str | None = None,
    page_url: str | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    resolved_champion_name = champion_name or get_page_title(soup)
    result: dict[str, Any] = {
        "name": resolved_champion_name,
        "page_url": page_url,
        "ultimate_identified": False,
        "ultimate": None,
        "ultimate_cooldown": None,
        "cooldown_label": None,
    }

    ultimate_containers = get_ultimate_containers(soup)
    if not ultimate_containers:
        result["error"] = "Could not find skill_r ability block."
        return result

    result["ultimate_identified"] = True
    ultimate_container = ultimate_containers[-1]
    stats: dict[str, dict[str, str]] = {}
    for container in ultimate_containers:
        candidate_stats = extract_ability_stats(container)
        if find_cooldown_stat(candidate_stats):
            ultimate_container = container
            stats = candidate_stats
            break

    ability_name = ultimate_container.select_one(".ability-info-stats__ability")
    result["ultimate"] = (
        clean_text(ability_name.get_text(" ", strip=True))
        if ability_name
        else None
    )

    if not stats:
        stats = extract_ability_stats(ultimate_container)

    cooldown_stat = find_cooldown_stat(stats)
    if not cooldown_stat:
        result["error"] = "Could not find a direct cooldown stat in the skill_r block."
        return result

    result["ultimate_cooldown"] = cooldown_stat["value"]
    result["cooldown_label"] = cooldown_stat["label"]
    return result


def scrape_champion_ultimate_cooldowns(
    source: str | Path,
    json_path: Path = DEFAULT_CHAMPION_ULT_JSON_PATH,
    subpage_cache_dir: Path = DEFAULT_CHAMPION_SUBPAGE_CACHE_DIR,
    image_dir: Path = DEFAULT_CHAMPION_IMAGE_DIR,
) -> dict[str, dict[str, Any]]:
    with requests.Session() as session:
        session.headers.update({"User-Agent": "JOAT-Tracker/1.0"})
        html = load_html(source, session)
        champions = extract_champion_links(html)

        champion_data: dict[str, dict[str, Any]] = {}
        for champion in champions:
            page_url = champion["page_url"]
            page_html = get_cached_subpage_html(page_url, session, subpage_cache_dir)
            data = extract_ultimate_cooldown_data(
                page_html,
                champion_name=champion["name"],
                page_url=page_url,
            )
            image_url = champion.get("image_url")
            data["image_url"] = image_url
            data["img_path"] = (
                str(download_image(image_url, champion["name"], image_dir, session))
                if image_url
                else None
            )
            champion_data[champion["name"]] = data

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(champion_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return champion_data


def scrape_single_champion_ultimate_cooldown(
    source: str | Path,
    json_path: Path = DEFAULT_CHAMPION_ULT_JSON_PATH,
) -> dict[str, dict[str, Any]]:
    with requests.Session() as session:
        session.headers.update({"User-Agent": "JOAT-Tracker/1.0"})
        html = load_html(source, session)

    data = extract_ultimate_cooldown_data(html, page_url=str(source))
    champion_name = data["name"] or Path(str(source)).stem
    champion_data = {champion_name: data}
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(champion_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return champion_data


def scrape_lol_items(
    source: str | Path = DEFAULT_SOURCE,
    image_dir: Path = DEFAULT_IMAGE_DIR,
    json_path: Path = DEFAULT_JSON_PATH,
    stats_path: Path = DEFAULT_STATS_PATH,
    subpage_cache_dir: Path = DEFAULT_SUBPAGE_CACHE_DIR,
) -> dict[str, dict[str, Any]]:
    valid_stats = set(load_valid_stats(stats_path))
    with requests.Session() as session:
        session.headers.update({"User-Agent": "JOAT-Tracker/1.0"})
        html = load_html(source, session)
        items = parse_item_divs(
            html,
            image_dir=image_dir,
            session=session,
            valid_stats=valid_stats,
            subpage_cache_dir=subpage_cache_dir,
        )
        save_items_json(items, json_path)
        return items


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape League of Legends wiki data.")
    parser.add_argument(
        "--mode",
        choices=["items", "champion-ults"],
        default="items",
        help="Scrape item data or champion ultimate cooldowns.",
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="Local HTML file path or page URL.",
    )
    parser.add_argument(
        "--champion-page",
        help="Scrape one saved champion page instead of reading champion links from --source.",
    )
    parser.add_argument(
        "--image-dir",
        default=str(DEFAULT_IMAGE_DIR),
        help="Directory where item images will be saved.",
    )
    parser.add_argument(
        "--json-path",
        default=None,
        help="Path for the output JSON file.",
    )
    parser.add_argument(
        "--stats-path",
        default=str(DEFAULT_STATS_PATH),
        help="Path to the canonical stats list.",
    )
    parser.add_argument(
        "--subpage-cache-dir",
        default=str(DEFAULT_SUBPAGE_CACHE_DIR),
        help="Directory where fetched item subpages will be cached.",
    )
    parser.add_argument(
        "--champion-subpage-cache-dir",
        default=str(DEFAULT_CHAMPION_SUBPAGE_CACHE_DIR),
        help="Directory where fetched champion subpages will be cached.",
    )
    parser.add_argument(
        "--champion-image-dir",
        default=str(DEFAULT_CHAMPION_IMAGE_DIR),
        help="Directory where champion icons will be saved.",
    )
    args = parser.parse_args()

    if args.mode == "champion-ults":
        json_path = Path(args.json_path) if args.json_path else DEFAULT_CHAMPION_ULT_JSON_PATH
        if args.champion_page:
            champions = scrape_single_champion_ultimate_cooldown(
                source=args.champion_page,
                json_path=json_path,
            )
        else:
            champions = scrape_champion_ultimate_cooldowns(
                source=args.source,
                json_path=json_path,
                subpage_cache_dir=Path(args.champion_subpage_cache_dir),
                image_dir=Path(args.champion_image_dir),
            )

        missing = [
            champion_name
            for champion_name, data in champions.items()
            if not data.get("ultimate_identified") or not data.get("ultimate_cooldown")
        ]
        print(f"Saved {len(champions)} champion ultimate cooldowns to {json_path}")
        if missing:
            print(f"Could not find direct ultimate cooldown for: {', '.join(missing)}")
        return

    item_json_path = Path(args.json_path) if args.json_path else DEFAULT_FULL_JSON_PATH
    items = scrape_lol_items(
        source=args.source,
        image_dir=Path(args.image_dir),
        json_path=item_json_path,
        stats_path=Path(args.stats_path),
        subpage_cache_dir=Path(args.subpage_cache_dir),
    )
    print(f"Saved {len(items)} items to {item_json_path}")


if __name__ == "__main__":
    main()
