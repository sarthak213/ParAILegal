"""Fetch the official texts of statutes that newer laws replaced, for cross-reference.

    python scripts/fetch_replaced_statutes.py <out dir>

India Code drops an Act once it is repealed, so the Indian Penal Code, the labour laws folded
into the four Labour Codes, the Aircraft Act and the rest are no longer there. Their last
consolidated India Code PDFs ("A1948-63.pdf" = Act 63 of 1948) survive in the Internet
Archive; this fetches the latest English copy of each, checks its title page, and writes
manifest.json for scripts/build_corpus.py --old-codes.
"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# (title, year, act number, replaced by)
REPLACED = [
    ("The Indian Penal Code", 1860, 45, "The Bharatiya Nyaya Sanhita, 2023"),
    ("The Code of Criminal Procedure, 1973", 1974, 2, "The Bharatiya Nagarik Suraksha Sanhita, 2023"),
    ("The Indian Evidence Act, 1872", 1872, 1, "The Bharatiya Sakshya Adhiniyam, 2023"),
    ("The Aircraft Act, 1934", 1934, 22, "The Bharatiya Vayuyan Adhiniyam, 2024"),
    # Code on Wages, 2019
    ("The Payment of Wages Act, 1936", 1936, 4, "The Code on Wages, 2019"),
    ("The Minimum Wages Act, 1948", 1948, 11, "The Code on Wages, 2019"),
    ("The Payment of Bonus Act, 1965", 1965, 21, "The Code on Wages, 2019"),
    ("The Equal Remuneration Act, 1976", 1976, 25, "The Code on Wages, 2019"),
    # Industrial Relations Code, 2020
    ("The Trade Unions Act, 1926", 1926, 16, "The Industrial Relations Code, 2020"),
    ("The Industrial Employment (Standing Orders) Act, 1946", 1946, 20, "The Industrial Relations Code, 2020"),
    ("The Industrial Disputes Act, 1947", 1947, 14, "The Industrial Relations Code, 2020"),
    # Code on Social Security, 2020
    ("The Employees' Compensation Act, 1923", 1923, 8, "The Code on Social Security, 2020"),
    ("The Employees' State Insurance Act, 1948", 1948, 34, "The Code on Social Security, 2020"),
    ("The Employees' Provident Funds and Miscellaneous Provisions Act, 1952", 1952, 19,
     "The Code on Social Security, 2020"),
    ("The Employment Exchanges (Compulsory Notification of Vacancies) Act, 1959", 1959, 31,
     "The Code on Social Security, 2020"),
    ("The Maternity Benefit Act, 1961", 1961, 53, "The Code on Social Security, 2020"),
    ("The Payment of Gratuity Act, 1972", 1972, 39, "The Code on Social Security, 2020"),
    ("The Cine-Workers Welfare Fund Act, 1981", 1981, 33, "The Code on Social Security, 2020"),
    ("The Building and Other Construction Workers' Welfare Cess Act, 1996", 1996, 28,
     "The Code on Social Security, 2020"),
    ("The Unorganised Workers' Social Security Act, 2008", 2008, 33, "The Code on Social Security, 2020"),
    # Occupational Safety, Health and Working Conditions Code, 2020
    ("The Factories Act, 1948", 1948, 63, "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Plantations Labour Act, 1951", 1951, 69, "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Mines Act, 1952", 1952, 35, "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Working Journalists and other Newspaper Employees (Conditions of Service) and "
     "Miscellaneous Provisions Act, 1955", 1955, 45,
     "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Motor Transport Workers Act, 1961", 1961, 27, "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Beedi and Cigar Workers (Conditions of Employment) Act, 1966", 1966, 32,
     "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Contract Labour (Regulation and Abolition) Act, 1970", 1970, 37,
     "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Sales Promotion Employees (Conditions of Service) Act, 1976", 1976, 11,
     "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Inter-State Migrant Workmen (Regulation of Employment and Conditions of Service) Act, 1979",
     1979, 30, "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Cine-Workers and Cinema Theatre Workers (Regulation of Employment) Act, 1981", 1981, 50,
     "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Dock Workers (Safety, Health and Welfare) Act, 1986", 1986, 54,
     "The Occupational Safety, Health and Working Conditions Code, 2020"),
    ("The Building and Other Construction Workers (Regulation of Employment and Conditions of "
     "Service) Act, 1996", 1996, 27, "The Occupational Safety, Health and Working Conditions Code, 2020"),
    # shipping laws of 2025
    ("The Indian Boilers Act, 1923", 1923, 5, "The Boilers Act, 2025"),
    ("The Indian Carriage of Goods by Sea Act, 1925", 1925, 26, "The Carriage of Goods by Sea Act, 2025"),
    ("The Indian Bills of Lading Act, 1856", 1856, 9, "The Bills of Lading Act, 2025"),
    ("The Merchant Shipping Act, 1958", 1958, 44, "The Merchant Shipping Act, 2025"),
    ("The Indian Ports Act, 1908", 1908, 15, "The Indian Ports Act, 2025"),
    ("The Coasting Vessels Act, 1838", 1838, 19, "The Coastal Shipping Act, 2025"),
    # others
    ("The Press and Registration of Books Act, 1867", 1867, 25,
     "The Press and Registration of Periodicals Act, 2023"),
    ("The Companies Act, 1956", 1956, 1, "The Companies Act, 2013"),
    ("The Monopolies and Restrictive Trade Practices Act, 1969", 1969, 54, "The Competition Act, 2002"),
    ("The Foreign Exchange Regulation Act, 1973", 1973, 46, "The Foreign Exchange Management Act, 1999"),
    ("The Consumer Protection Act, 1986", 1986, 68, "The Consumer Protection Act, 2019"),
    ("The Indian Medical Council Act, 1956", 1956, 102, "The National Medical Commission Act, 2019"),
    ("The Dentists Act, 1948", 1948, 16, "The National Dental Commission Act, 2023"),
    ("The Land Acquisition Act, 1894", 1894, 1, "The Right to Fair Compensation and Transparency in Land "
     "Acquisition, Rehabilitation and Resettlement Act, 2013"),
    ("The Arbitration Act, 1940", 1940, 10, "The Arbitration and Conciliation Act, 1996"),
    ("The Juvenile Justice (Care and Protection of Children) Act, 2000", 2000, 56,
     "The Juvenile Justice (Care and Protection of Children) Act, 2015"),
    ("The Mental Health Act, 1987", 1987, 14, "The Mental Healthcare Act, 2017"),
    ("The Persons with Disabilities (Equal Opportunities, Protection of Rights and Full "
     "Participation) Act, 1995", 1996, 1, "The Rights of Persons with Disabilities Act, 2016"),
    ("The Sick Industrial Companies (Special Provisions) Act, 1985", 1986, 1,
     "The Insolvency and Bankruptcy Code, 2016"),
    ("The Income-tax Act, 1961", 1961, 43, "The Income-tax Act, 2025"),
    ("The Foreigners Act, 1946", 1946, 31, "The Immigration and Foreigners Act, 2025"),
    ("The Passport (Entry into India) Act, 1920", 1920, 34, "The Immigration and Foreigners Act, 2025"),
    ("The Registration of Foreigners Act, 1939", 1939, 16, "The Immigration and Foreigners Act, 2025"),
    ("The Prevention of Food Adulteration Act, 1954", 1954, 37, "The Food Safety and Standards Act, 2006"),
]

CDX = "https://web.archive.org/cdx/search/cdx"
USER_AGENT = "ParAILegal corpus builder (+https://github.com/sarthak213/ParAILegal)"


def slug(title: str) -> str:
    t = re.sub(r"^the\s+", "", title.lower())
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def get(url: str, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def captures(year: int, number: int) -> list[tuple[str, str, int]]:
    """Archived English India Code PDFs of Act `number` of `year`: (timestamp, url, length)."""
    name = re.compile(rf"/[Aa]_?{year}[-_]+0*{number}(?:\s*\(\d\))?\.pdf$")
    found, other = [], []
    for host in ("www.indiacode.nic.in", "indiacode.nic.in"):
        q = urllib.parse.urlencode({"url": f"{host}/bitstream/", "matchType": "prefix",
                                    "filter": [f"original:.*{year}[-_]+0*{number}.*", "mimetype:application/pdf"],
                                    "fl": "timestamp,original,length"}, doseq=True)
        for line in get(f"{CDX}?{q}").decode().splitlines():
            ts, url, length = line.split(" ")
            if not length.isdigit() or int(length) < 20_000 or re.search(r"/[Hh][^/]*$", url):
                continue  # too small to be the Act, or the Hindi text ("H1948-63.pdf")
            (found if name.search(urllib.parse.unquote(url)) else other).append((ts, url, int(length)))
        time.sleep(1)
    # India Code's own file names first, newest first; then any other copy (the title check decides)
    return sorted(set(found), reverse=True) + sorted(set(other), reverse=True)


def captures_by_name(title: str, year: int) -> list[tuple[str, str, int]]:
    """Copies filed under a descriptive name ("the_companies_act,_1956.pdf"): search for the
    title's most distinctive word and the year in the file name."""
    words = sorted((w for w in re.findall(r"[a-z]+", title.lower())
                    if len(w) > 4 and w not in ("indian", "provisions", "regulation", "conditions")),
                   key=len, reverse=True)
    if not words:
        return []
    found = []
    for host in ("www.indiacode.nic.in", "indiacode.nic.in"):
        q = urllib.parse.urlencode({"url": f"{host}/bitstream/", "matchType": "prefix",
                                    "filter": [f"original:(?i).*{words[0]}.*{year}.*", "mimetype:application/pdf"],
                                    "fl": "timestamp,original,length"}, doseq=True)
        for line in get(f"{CDX}?{q}").decode().splitlines():
            ts, url, length = line.split(" ")
            if length.isdigit() and int(length) > 20_000 and not re.search(r"/[Hh][^/]*$", url):
                found.append((ts, url, int(length)))
        time.sleep(1)
    return sorted(set(found), reverse=True)


def title_matches(pdf: bytes, title: str) -> bool:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(io.BytesIO(pdf))
    first = " ".join((reader.pages[i].extract_text() or "") for i in range(min(3, len(reader.pages))))
    key = lambda s: re.sub(r"[^a-z]", "", s.lower())  # noqa: E731
    words = [w for w in re.findall(r"[a-z]+", title.lower()) if len(w) > 3 and w not in ("indian",)][:4]
    return all(w in key(first) for w in words)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("replaced-statutes")
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for title, year, number, replaced_by in REPLACED:
        name = f"{slug(title)}.pdf"
        if name in manifest and (out / name).exists():
            continue
        try:
            options = captures(year, number) or captures_by_name(title, year)
        except Exception as exc:
            print(f"  ! {title}: index lookup failed: {exc}")
            continue
        got = False
        for ts, url, _ in options[:10]:
            try:
                pdf = get(f"https://web.archive.org/web/{ts}id_/{url}")
            except Exception:
                continue
            try:
                ok = pdf[:5] == b"%PDF-" and title_matches(pdf, title)
            except Exception:  # a truncated or broken capture
                ok = False
            if ok:
                (out / name).write_bytes(pdf)
                manifest[name] = {"title": title, "year": str(year), "act_number": str(number),
                                  "replaced_by": replaced_by, "source": url, "archived": ts}
                print(f"  {title}: {len(pdf):,} bytes ({ts[:8]})")
                got = True
                break
            time.sleep(1)
        if not got:
            print(f"  ! {title}: no archived English PDF found ({len(options)} candidates)")
        manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(manifest)} of {len(REPLACED)} replaced statutes in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
