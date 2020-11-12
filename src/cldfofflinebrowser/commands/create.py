"""
Create an offline browseable version of a CLDF Wordlist.
"""
import shutil
import pathlib
import textwrap
import itertools
import collections

from tqdm import tqdm
from cldfbench.cli_util import add_dataset_spec, get_dataset

import cldfofflinebrowser
from cldfofflinebrowser import osmtiles
from cldfofflinebrowser.template import render
from cldfofflinebrowser import media


def register(parser):
    parser.add_argument(
        '--outdir',
        help="Directory in which to create the offline browseable files.",
        default='offline')
    parser.add_argument(
        '--with-tiles',
        help="Also download map tiles (requires {})".format(osmtiles.CMD),
        action='store_true',
        default=False)
    parser.add_argument(
        '--include',
        help="Whitespace separated list of parameter IDs",
        type=lambda s: s.split(),
        default=None)
    add_dataset_spec(parser)
    parser.add_argument(
        '--padding',
        default=8,
        help="Padding in degree longitude at zoom level 5 to add to minimal bounding box when "
             "retrieving map tiles.",
        type=int)
    parser.add_argument(
        '--max-zoom',
        default=10,
        help="Maximal zoom level for which to add map tiles (must be < 13)",
        type=int)
    #
    # FIXME: configuration? Name of the media FK column?
    # sorting of markers?
    #


def run(args):
    args.include = args.include.split() if args.include else None

    ds = get_dataset(args)
    cldf = ds.cldf_reader()
    # We expect a list of audio files in a table "media.csv", with a column "mimetype".
    audio, form2audio = media.read_media_files(
        cldf, filter=lambda r: r['mimetype'].startswith('audio/'))
    title = textwrap.shorten(cldf.properties['dc:title'], width=60, placeholder='…')

    outdir = pathlib.Path(args.outdir)
    if not outdir.exists():
        outdir.mkdir()

    for sub in ['tiles', 'static']:
        sub = outdir / sub
        if not sub.exists():
            sub.mkdir()

    for p in pathlib.Path(cldfofflinebrowser.__file__).parent.joinpath('static').iterdir():
        shutil.copy(str(p), str(outdir / 'static' / p.name))

    parameters = {}
    for p in cldf.iter_rows('ParameterTable', 'id', 'name'):
        if (args.include is None) or (p['id'] in args.include):
            p.update(representation=set(), has_audio=False)
            parameters[p['id']] = p

    languages, coords = {}, []
    for p in cldf.iter_rows('LanguageTable', 'latitude', 'longitude', 'id', 'name'):
        for c in ['Latitude', 'Longitude']:
            if c in p:
                del p[c]
        coords.append((p['latitude'], p['longitude']))
        p['latitude'] = float(p['latitude'])
        p['longitude'] = float(p['longitude'])
        languages[p['ID']] = p

    if args.with_tiles:
        try:
            tiles = osmtiles.TileList(outdir / 'tiles' / 'tilelist.yaml')
        except FileNotFoundError:
            args.log.error(
                'The command {} is not installed on your system. '
                'Either install it or do not use the --with-tiles flag.'.format(
                    osmtiles.CMD))
            return
        tiles.create(coords, args.max_zoom, padding=args.padding)
        missing = tiles.prune()
        if missing:
            args.log.info('Must download {} tiles'.format(missing))
            tiles.download()

    #
    # FIXME: looping over FormTable means we only support Wordlist!
    #
    for pid, forms in tqdm(itertools.groupby(
        sorted(
            cldf.iter_rows('FormTable', 'id', 'languageReference', 'parameterReference', 'form'),
            key=lambda r: (r['parameterReference'], r['id'])),
        lambda r: r['Parameter_ID'],
    )):
        if args.include and (pid not in args.include):
            continue
        audios = []
        data = {
            'languages': {},
            'forms': collections.defaultdict(dict),
        }
        pout = outdir / 'parameter-{}'.format(pid)
        if not pout.exists():
            pout.mkdir()

        for form in forms:
            data['forms'][form['languageReference']] = {
                'form': form['form'],
                'audio': False,
            }
            data['languages'][form['languageReference']] = languages[form['languageReference']]
            parameters[pid]['representation'].add(form['languageReference'])
            if 'Audio_Files' in form:
                # Audio files may either be linked via a list-valued foreign key column
                # "Audio_Files" ...
                audio_files = [audio[aid] for aid in form['Audio_Files']]
            else:
                # ... or via a formReference in the media table:
                audio_files = form2audio.get(form['id'], [])
            audio_file = media.get_best_audio(audio_files)
            if audio_file:
                media.download(
                    cldf,
                    audio_file,
                    pout,
                    '{}.mp3'.format(form['languageReference']))
                data['forms'][form['languageReference']]['audio'] = True
                parameters[pid]['has_audio'] = True

        render(
            pout,
            'data.js',
            data=data,
            options={'minZoom': 0, 'maxZoom': args.max_zoom})
        render(
            pout / 'index.html',
            'parameter.html',
            parameter=parameters[pid],
            index=False,
            audios=audios,
            data=data,
            parameters=parameters.items(),
            title=title,
        )
    render(
        outdir,
        'index.html',
        parameters=parameters.items(),
        index=True,
        title=title,
    )
