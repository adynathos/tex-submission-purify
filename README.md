 
# Tex Submission Purifier

Prepares a Latex article for submission to arxiv.org, inspired by <https://github.com/google-research/arxiv-latex-cleaner>.
Removes comments from `.tex` files and copies only the referenced resources.

Features:

* recursive descent into files imported with `\input`
* remove inline comments starting with `%` as well as  `\comment` blocks
* remove other specified commands
* short-circuit commands (for example `Hello \kl{world}!` -> `Hello world!`)
* copy only the files referenced by `\input` and `\includegraphics`
* notification about unused files

Right now the images are not altered.

## Dependencies

We use [TexSoup](https://github.com/alvinwan/TexSoup) for tex parsing and [click](https://click.palletsprojects.com/en/7.x/) for console arguments.

```
pip install TexSoup click
```


## CLI

The program can be invoked from the command line in the following way:

```bash
python3 tex_submission_purify.py src_root_document.tex output_directory
```

Configuration options:

`--remove-comments-completely`  
By default we leave the `%` in place of a comment, to prevent empty lines from confusing Latex.
This option will remove the while comment including the `%`.

`--remove-cmd cmdA,cmdB,cmdC`  
The invocations of `cmdA`, `cmdB`, `cmdC` will be all removed. (for example `\cmdA{something}`).
Separate commands with `,` but not with spaces.

`--short-circuit-cmd cmdA,cmdB`  
The invocations of `cmdA`, `cmdB` are replaced with their contents.
For example `This \cmdA{word} is special` is transformed to `This word is special`.
Separate commands with `,` but not with spaces.

`--keep-file some_file.txt`  
The file will be copied to the output directory even if it is not referenced.
Multiple files can be specified by repeating this option.

`--out-root-doc-name out_name`  
By default the output root document is renamed to `ms.tex`. You can specify a different name here.

`--clear-out-dir`  
The output directory is deleted and re-created before running.

Example command for the test file:

```bash
python3 tex_submission_purify.py \
	test_src/comment_parsing_test.tex \
	/tmp/tex_submission_test_out \
	--remove-cmd KL \
	--short-circuit-cmd kl \
	--out-root-doc-name out_doc \
	--clear-out-dir \
	--keep-empty-comments
```

## Python interface

```python
from tex_submission_purify import TexSubmissionCleaner

c = TexSubmissionCleaner(
	'test_src/comment_parsing_test.tex',
	'/tmp/tex_submission_test_out',
)

c.clear_out_dir()

c.keep_empty_comments = True

c.commands_to_remove('KL')
c.commands_to_short_circuit('kl')

c.additional_files_to_keep(
	'ieee.bst',
)
c.run()

c.notify_about_unused_files()

c.print_statistics()

```




