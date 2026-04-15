"""Phase 2 text-extractor tests. Run: python3 test_signals_text.py"""
from __future__ import annotations
import sys
from signals_text import (
    extract_orientation, has_light_hint, extract_building_year,
    extract_condominium_fee, extract_energy_class, extract_all,
)
from signals import light_score

_failures: list = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    if not cond:
        _failures.append(name)
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


# -- Orientation ---------------------------------------------------------------
print("# orientation")
check("virado a nascente e poente",
      extract_orientation("virado a nascente e poente") == "E,W")
check("orientação solar nascente/sul",
      extract_orientation("orientação solar nascente/sul") == "S,E")
check("dupla exposição",
      extract_orientation("apartamento com dupla exposição") == "DUAL")
check("single sul",
      extract_orientation("apartamento virado a sul, muito luminoso") == "S")
check("no orientation",
      extract_orientation("apartamento bonito no centro") is None)
check("handles HTML",
      extract_orientation("<p>orientação <br/> nascente</p>") == "E")


# -- Light hint ----------------------------------------------------------------
print("# light_hint")
check("muita luz natural", has_light_hint("com muita luz natural"))
check("excelente luminosidade", has_light_hint("excelente luminosidade"))
check("soalheiro", has_light_hint("apartamento soalheiro"))
check("no hint", not has_light_hint("apartamento com cozinha equipada"))


# -- Building year -------------------------------------------------------------
print("# building_year")
check("construção em 2023",
      extract_building_year("Edifício de construção em 2023") == 2023)
check("construído em 1965",
      extract_building_year("prédio construído em 1965") == 1965)
check("ano de construção 2010",
      extract_building_year("Ano de construção: 2010") == 2010)
check("década 70 → 1970",
      extract_building_year("prédio dos anos 70") == 1970)
check("década 10 → 2010",
      extract_building_year("edifício dos anos 10") == 2010)
check("no year",
      extract_building_year("apartamento bonito") is None)
check("bogus year rejected",
      extract_building_year("referência 9999") is None)


# -- Condominium fee -----------------------------------------------------------
print("# condominium_fee")
check("Valor condomínio 43€/mês",
      extract_condominium_fee("Valor condomínio 43€/mês") == 43.0)
check("Condomínio: 45€/mês",
      extract_condominium_fee("Condomínio: 45€/mês") == 45.0)
check("Condomínio económico: apenas 30€/mês",
      extract_condominium_fee("Condomínio económico: apenas 30€/mês") == 30.0)
check("condomínio acessível (50€/mês)",
      extract_condominium_fee("condomínio acessível (50€/mês) e luminosidade") == 50.0)
check("Condomínio organizado (30 euros mensais)",
      extract_condominium_fee("Condomínio organizado (30 euros mensais)") == 30.0)
check("Valor do condomínio 68€",
      extract_condominium_fee("Valor do condomínio 68€") == 68.0)
check("no condomínio mention",
      extract_condominium_fee("apartamento bonito") is None)
check("rejects non-fee numbers near condomínio",
      # "500 m² em condomínio privado" — 500 is too far from € symbol
      extract_condominium_fee("500 m² em condomínio privado") is None)


# -- Energy class --------------------------------------------------------------
print("# energy_class")
check("Certificação Energética: B-",
      extract_energy_class("- Certificação Energética: B-") == "B-")
check("Categoria Energética: B",
      extract_energy_class("Categoria Energética: B ref:") == "B")
check("Classe energética A",
      extract_energy_class("eletrodomésticos de classe energética A") == "A")
check("A+",
      extract_energy_class("Certificação Energética A+") == "A+")
check("no class",
      extract_energy_class("apartamento bonito") is None)


# -- extract_all sanity --------------------------------------------------------
print("# extract_all")
all_res = extract_all("Edifício de construção em 2023, Certificação Energética: A+, "
                      "orientação solar nascente/sul, muita luz natural, "
                      "condomínio 55€/mês")
check("all five fields found",
      all_res == {
          "orientation": "S,E",
          "light_hint": True,
          "building_year": 2023,
          "condominium_fee": 55.0,
          "energy_class": "A+",
      }, detail=str(all_res))


# -- light_score ---------------------------------------------------------------
print("# light_score")
check("sul + floor 5 + hint → high",
      95 <= light_score({"orientation": "S", "floor": 5, "light_hint": True}) <= 100)
check("norte + floor 0 → low",
      light_score({"orientation": "N", "floor": 0}) < 15)
check("dual orientation + floor 3 → good",
      light_score({"orientation": "S,E", "floor": 3}) >= 65)
check("missing orientation, floor 4 → neutral-ish",
      45 <= light_score({"floor": 4}) <= 70)
check("all missing → None",
      light_score({}) is None)


print()
if _failures:
    print(f"FAILURES: {len(_failures)}")
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1)
print("All text-signal tests passed.")
