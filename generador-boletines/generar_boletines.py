"""Genera los seis boletines de muestra de Carta Clara como archivos HTML."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


CARPETA_RAIZ = Path(__file__).resolve().parent
RUTA_DATOS = CARPETA_RAIZ / "datos_ejemplo.json"
CARPETA_SALIDA_PREDETERMINADA = CARPETA_RAIZ / "salidas"


def cargar_modelos() -> dict[str, dict]:
    """Carga las configuraciones editables de los boletines."""
    with RUTA_DATOS.open(encoding="utf-8") as archivo:
        return json.load(archivo)


def escapar(valor: str) -> str:
    return html.escape(valor, quote=True)


def construir_html(clave: str, modelo: dict) -> str:
    """Construye un HTML autocontenido y compatible con clientes de correo."""
    bloques = "\n".join(
        f"""
        <tr>
          <td style="padding:24px 0;border-top:1px solid #e8e3ea;">
            <h2 style="font:700 18px Arial,sans-serif;margin:0 0 9px;color:#25232b;">{escapar(seccion['titulo'])}</h2>
            <p style="font:16px/1.6 Arial,sans-serif;margin:0;color:#57515c;">{escapar(seccion['texto'])}</p>
          </td>
        </tr>"""
        for seccion in modelo["secciones"]
    )
    return f"""<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escapar(modelo['asunto'])}</title></head>
<body style="margin:0;background:#f3f0ec;color:#25232b;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f0ec;"><tr><td style="padding:30px 12px;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;margin:auto;background:#ffffff;border-radius:16px;overflow:hidden;">
      <tr><td style="padding:24px 30px;border-bottom:1px solid #e8e3ea;font:700 18px Arial,sans-serif;">carta<span style="color:#6645b4;">.</span> clara <span style="float:right;font-size:11px;letter-spacing:1px;">{escapar(clave.upper())} · MUESTRA</span></td></tr>
      <tr><td style="padding:42px 30px 24px;"><p style="font:700 12px Arial,sans-serif;letter-spacing:1.5px;text-transform:uppercase;color:#6645b4;margin:0 0 13px;">{escapar(modelo['etiqueta'])}</p><h1 style="font:400 43px/1.05 Georgia,serif;letter-spacing:-1.3px;margin:0 0 18px;">{escapar(modelo['asunto'])}</h1><p style="font:18px/1.55 Arial,sans-serif;color:#625c67;margin:0;">{escapar(modelo['entradilla'])}</p></td></tr>
      <tr><td style="padding:0 30px 36px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{bloques}</table>
        <div style="background:{escapar(modelo['color_destacado'])};border-radius:12px;padding:18px 20px;font:14px/1.5 Arial,sans-serif;">Ejemplo demostrativo: adapta las fuentes, enlaces y llamadas a la acción antes de enviar.</div>
      </td></tr>
      <tr><td style="padding:22px 30px;background:#25232b;color:#ddd6e2;font:13px/1.5 Arial,sans-serif;">Ejemplo ficticio de Carta Clara · {escapar(modelo['aviso'])}</td></tr>
    </table>
  </td></tr></table>
</body></html>"""


def generar(modelos: dict[str, dict], carpeta_salida: Path, tipo: str | None) -> list[Path]:
    """Genera todos los modelos o solo el solicitado."""
    if tipo and tipo not in modelos:
        disponibles = ", ".join(modelos)
        raise ValueError(f"No existe el tipo '{tipo}'. Disponibles: {disponibles}.")
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    seleccion = {tipo: modelos[tipo]} if tipo else modelos
    creados = []
    for clave, modelo in seleccion.items():
        destino = carpeta_salida / f"{clave}.html"
        destino.write_text(construir_html(clave, modelo), encoding="utf-8")
        creados.append(destino)
    return creados


def argumentos() -> argparse.Namespace:
    analizador = argparse.ArgumentParser(description="Genera boletines HTML de Carta Clara.")
    analizador.add_argument("--tipo", help="Genera solo un tipo: radar, aula, entrena, idioma, equipo o pausa.")
    analizador.add_argument("--carpeta-salida", type=Path, default=CARPETA_SALIDA_PREDETERMINADA, help="Carpeta donde se guardarán los HTML.")
    analizador.add_argument("--listar", action="store_true", help="Muestra los modelos disponibles y termina.")
    return analizador.parse_args()


def main() -> None:
    opciones = argumentos()
    modelos = cargar_modelos()
    if opciones.listar:
        print("Modelos disponibles:")
        for clave, modelo in modelos.items():
            print(f"- {clave}: {modelo['nombre']}")
        return
    try:
        creados = generar(modelos, opciones.carpeta_salida, opciones.tipo)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print("Boletines generados:")
    for archivo in creados:
        print(f"- {archivo}")


if __name__ == "__main__":
    main()
