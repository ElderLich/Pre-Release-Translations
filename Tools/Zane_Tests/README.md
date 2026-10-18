# AutoPR móvil para traducciones ES

Esta variante funciona con opciones numeradas en la terminal de Android/Termux.
No abre una interfaz gráfica. Permite sincronizar, editar y subir las traducciones
Standard y Rush con una conexión lenta.

## Instalar o actualizar

1. Instala Python y `requests` en Termux: `pkg install python`, seguido de
   `python -m pip install requests`.
2. Copia este `AutoPR.py` y [`Tools/PromptPatcher.py`](../PromptPatcher.py) a la
   misma carpeta. Si ya tienes el repositorio, puedes ejecutar directamente
   `python Tools/Zane_Tests/AutoPR.py`.
3. Ejecuta `python AutoPR.py` y elige **Standard** o **Rush**. Para actualizar el
   programa, reemplaza esos dos archivos; conserva tus archivos de traducción.

No hace falta clonar todo el repositorio ni instalar Git. Si usas el almacenamiento
compartido de Android, concede acceso con `termux-setup-storage`.

En Android se conserva la carpeta de trabajo habitual:
`/storage/emulated/0/pre_updates/Prelanzadas/AutoPR`. En otros sistemas se usa
`workspace` junto al script. Los archivos que debes editar son:

- Standard: `raw2/test-release.cdb` y `raw2/test-strings.conf`.
- Rush: `Rush/raw2/rush-pre-release.cdb`.
- Mappings Standard: `Mappings.csv` dentro de la carpeta de trabajo.

## Conexión y publicación

Sincroniza antes de editar y vuelve a sincronizar antes de subir si otra persona
ha cambiado esos archivos. Se conservan las traducciones locales; revisa los
conflictos que indique el programa. Las descargas se verifican por SHA y se
reutilizan cuando vuelves a intentar una operación interrumpida. Los archivos
remotos sin cambios no se vuelven a descargar.

Standard descarga `ES/Workspace` de GitHub. Primero, **Sync ES Workspace** debe
importar las nuevas cartas y claves de strings desde CN; **Release ES** solo
compila las traducciones existentes. Cuando no cambian los archivos de GitHub,
el script muestra el estado del importador y la última importación correcta,
sin afirmar que la fuente CN esté al día. Si la importación sigue en curso,
vuelve a sincronizar cuando termine. También puedes ejecutar **Sync ES Workspace**
desde Actions para solicitar una importación inmediata. El control diario se
cuenta desde la última importación correcta; subir traducciones no lo reinicia.

Al actualizar desde la versión anterior, la primera sincronización recupera la
base anterior a partir de los SHA guardados. Puede necesitar una descarga
adicional una sola vez. Si no existe un SHA anterior válido, conserva el texto
español local y establece una base para las siguientes sincronizaciones;
revisa las traducciones que ya tenías en ese primer uso.

Las lecturas públicas no necesitan token. Para subir, guarda tu token de GitHub
en `~/.github_token` o define `GITHUB_TOKEN`/`GH_TOKEN`. El token necesita acceso
al repositorio con permiso **Contents: Read and write**. No lo pongas en el
script ni en archivos que vayas a compartir.

Los archivos seleccionados se suben juntos en un solo commit. Si cambiaron en
GitHub desde tu última sincronización, la subida se detiene para que revises
esos cambios. Una subida idéntica no crea otro commit. El programa nunca fuerza
la rama.

El script ya no pide un número de versión ni crea tags `ES-v…`. GitHub Actions
compila el paquete y asigna el tag cuando hay cambios en los archivos generados.
Comprueba el resultado en [Actions](https://github.com/ElderLich/Pre-Release-Translations/actions).
Una subida del workspace no garantiza un paquete nuevo si la compilación falla
o no cambian sus resultados.

## Configuración opcional

Coloca `config.json` dentro de tu carpeta de trabajo, por ejemplo:

```json
{
  "github_owner": "ElderLich",
  "github_repo": "Pre-Release-Translations",
  "branch": "main",
  "cards_cdb": "/storage/emulated/0/pre_updates/Prelanzadas/cards.cdb",
  "connect_timeout": 20,
  "read_timeout": 120
}
```

También puedes definir `base_dir`, `mappings_csv` o `token_file`. Para encontrar
la configuración en otra ubicación, usa `ZANE_AUTOPR_CONFIG`; para cambiar la
carpeta de trabajo inicial, usa `ZANE_AUTOPR_BASE_DIR`. Los workflows oficiales
publican desde `main`.

No se crean carpetas de backups. `.download-cache` conserva las descargas
verificadas; `.standard-baseline.cdb`, `.standard-baseline.conf` y
`Rush/Base Files` permiten distinguir tus ediciones de cambios publicados.
Son datos de sincronización que se reemplazan, no un historial de copias. Si
eliminas la caché, la siguiente sincronización volverá a descargar lo necesario.
