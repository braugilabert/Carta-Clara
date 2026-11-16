# Generador de boletines

Este generador crea versiones HTML de las seis newsletters demostrativas de Carta Clara. No envía correos ni conecta con ninguna plataforma externa: prepara archivos para revisar, adaptar o importar en una herramienta de email.

## Requisitos

Solo necesitas Python 3. No instala paquetes.

## Uso

Desde la carpeta raíz del proyecto:

```powershell
python generador-boletines/generar_boletines.py
```

El resultado se guarda en `generador-boletines/salidas/`.

Para generar un único modelo:

```powershell
python generador-boletines/generar_boletines.py --tipo radar
```

Para conocer los nombres disponibles:

```powershell
python generador-boletines/generar_boletines.py --listar
```

## Qué editar

Los textos de muestra están en [`datos_ejemplo.json`](datos_ejemplo.json). Sustituye asunto, entradilla, secciones y avisos antes de usar un boletín real.

## Modelos incluidos

- `radar`: novedades profesionales con contexto y fuentes.
- `aula`: aprendizaje breve para academias o docentes.
- `entrena`: contenido editorial para centros deportivos.
- `idioma`: práctica breve para aprender idiomas.
- `equipo`: comunicación interna de pequeños equipos.
- `pausa`: lectura editorial para personas curiosas.

Antes de enviar, revisa fuentes, permisos, privacidad, destinatarios y mecanismo de baja aplicable.
