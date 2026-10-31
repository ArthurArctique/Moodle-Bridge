#!/usr/bin/env python3
"""Report d'une note découverte dans le classeur de notes (.xlsx), appelé par le pont avec un Python muni d'openpyxl.

Entrée, en JSON sur l'entrée standard : {"path", "sheet", "element", "note", "value"}.
Sortie, une ligne JSON : {"status": "written" | "unchanged" | "locked" | "busy" | "error", "cell", "previous",
"backup", "message"}.

Garanties :
- rien n'est écrit si Excel a le classeur ouvert (fichier ~$…) ;
- seule change la case « Note k » de la ligne de l'élément, repérée par les en-têtes, et jamais si elle contient
  une formule ;
- le classeur réécrit est relu et comparé à l'original (feuilles, valeurs et formules de toutes les cases,
  validations, fusions, mises en forme conditionnelles, volets figés, largeurs, noms définis) ;
- sauvegarde horodatée de l'original à côté de lui, puis remplacement atomique, seulement si le fichier n'a pas
  changé depuis sa lecture. Le recalcul est demandé à la prochaine ouverture (fullCalcOnLoad).
"""

import io
import json
import os
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import openpyxl

HEADER_SCAN = 30


def out(status, **fields):
    print(json.dumps({"status": status, **fields}, ensure_ascii=False))
    return 0


def norm(text):
    return unicodedata.normalize("NFC", str(text or "")).strip().casefold()


def locate(ws, element, note):
    """Case « Note k » de la ligne dont la colonne « Élément » vaut le code donné."""
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, HEADER_SCAN)):
        headers = {norm(c.value): c.column for c in row if c.value is not None}
        if norm("Élément") in headers and norm(f"Note {note}") in headers:
            header_row, element_col, note_col = row[0].row, headers[norm("Élément")], headers[norm(f"Note {note}")]
            break
    else:
        raise LookupError(f"en-têtes « Élément » et « Note {note} » introuvables")
    rows = [r for r in range(header_row + 1, ws.max_row + 1) if norm(ws.cell(r, element_col).value) == norm(element)]
    if len(rows) != 1:
        raise LookupError(f"élément « {element} » : {len(rows)} ligne(s) au lieu d'une")
    return ws.cell(rows[0], note_col)


def _value(value):
    return str(value) if hasattr(value, "text") else value  # formules matricielles


def fingerprint(wb):
    """Tout ce qu'une réécriture par openpyxl ne doit pas changer."""
    data = {"sheets": wb.sheetnames, "names": sorted(map(str, wb.defined_names))}
    for ws in wb.worksheets:
        data[ws.title] = {
            "cells": {c.coordinate: _value(c.value) for row in ws.iter_rows() for c in row if c.value is not None},
            "validations": sorted((str(dv.sqref), dv.type, str(dv.formula1), str(dv.formula2))
                                  for dv in ws.data_validations.dataValidation),
            "merged": sorted(map(str, ws.merged_cells.ranges)),
            "conditional": sorted(str(cf.sqref) for cf in ws.conditional_formatting),
            "freeze": str(ws.freeze_panes),
            "widths": {key: dim.width for key, dim in ws.column_dimensions.items()},
            "names": sorted(map(str, ws.defined_names)),
        }
    return data


def backup_path(path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    n = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.backup-{stamp}-{n}{path.suffix}")
        n += 1
    return candidate


def write(request):
    path = Path(request["path"])
    lock = path.with_name("~$" + path.name)
    if lock.exists():
        return out("locked", message="Excel a le classeur ouvert")
    if not path.exists():
        return out("error", message=f"classeur introuvable : {path}")
    before = path.stat().st_mtime_ns
    original = path.read_bytes()
    wb = openpyxl.load_workbook(io.BytesIO(original))
    if request["sheet"] not in wb.sheetnames:
        return out("error", message=f"feuille « {request['sheet']} » introuvable")
    ws = wb[request["sheet"]]
    try:
        cell = locate(ws, request["element"], int(request["note"]))
    except LookupError as exc:
        return out("error", message=str(exc))
    value = round(float(request["value"]), 2)
    value = int(value) if value.is_integer() else value
    previous = cell.value
    if isinstance(previous, str) and previous.startswith("="):
        return out("error", cell=cell.coordinate, message=f"la case {cell.coordinate} contient une formule")
    if isinstance(previous, (int, float)) and not isinstance(previous, bool) and abs(previous - value) < 1e-9:
        return out("unchanged", cell=cell.coordinate, previous=previous)

    expected = fingerprint(wb)
    expected[ws.title]["cells"][cell.coordinate] = value
    cell.value = value
    wb.calculation.fullCalcOnLoad = True
    buffer = io.BytesIO()
    wb.save(buffer)
    data = buffer.getvalue()
    found = fingerprint(openpyxl.load_workbook(io.BytesIO(data)))
    if found != expected:
        differing = sorted(k for k in set(found) | set(expected) if found.get(k) != expected.get(k))
        return out("error", cell=cell.coordinate, message=f"contrôle d'intégrité échoué ({', '.join(differing)})")

    if lock.exists():
        return out("locked", message="Excel a ouvert le classeur pendant l'écriture")
    if path.stat().st_mtime_ns != before:
        return out("busy", message="classeur modifié pendant l'écriture")
    backup = backup_path(path)
    with open(backup, "xb") as handle:
        handle.write(original)
    shutil.copystat(path, backup)
    tmp = path.with_name(f".{path.name}.moodle-bridge.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    shutil.copymode(path, tmp)
    if path.stat().st_mtime_ns != before or lock.exists():
        tmp.unlink()
        return out("busy", message="classeur modifié pendant l'écriture", backup=str(backup))
    os.replace(tmp, path)
    return out("written", cell=cell.coordinate,
               previous=previous if isinstance(previous, (int, float, str)) else None, backup=str(backup))


def main():
    try:
        return write(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 — toujours répondre en JSON au pont
        return out("error", message=f"{type(exc).__name__} : {exc}"[:300])


if __name__ == "__main__":
    sys.exit(main())
