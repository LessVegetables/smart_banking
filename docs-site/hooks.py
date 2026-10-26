"""MkDocs hooks for the docs site (docs-site/mkdocs.yml)."""

from mkdocs.structure.files import File, InclusionLevel

# The root README.md and the landing index.md both map to /index.html, so MkDocs would drop the README.
# mkdocs.yml excludes it from the walk; here it comes back under /guide/. Links to README.md still resolve.
# The explicit inclusion keeps MkDocs from applying exclude_docs to it again after this hook.
README_DEST = 'guide/index.html'


def on_files(files, config):
    if files.get_file_from_path('README.md') is None:
        files.append(
            File(
                'README.md',
                config['docs_dir'],
                config['site_dir'],
                config['use_directory_urls'],
                dest_uri=README_DEST,
                inclusion=InclusionLevel.INCLUDED,
            )
        )
    return files
