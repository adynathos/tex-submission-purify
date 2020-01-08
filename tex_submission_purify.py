#!/usr/bin/env python3
from pathlib import Path

from collections import deque, namedtuple, Counter
from enum import Enum
from shutil import rmtree, copy as copy_file

from TexSoup import TexSoup
from TexSoup.data import TokenWithPosition, TexNode
import click


class NodeAction(Enum):
	Continue = 0
	StopDescent = 3

LATEX_COMPILATION_FILES_TO_IGNORE = ['.aux', '.blg', '.log', '.synctex.gz', '.pdf']
LATEX_COMPILATION_FILES_TO_KEEP = ['.bbl', '.brf']

class TexSubmissionCleaner:
	FileQueueEntry = namedtuple('FileQueueEntry', ['path', 'type'])

	FILE_TYPE_TEX = 'tex'
	FILE_TYPE_GRAPHICS = 'graphics'
	FILE_TYPE_OPAQUE = 'copy'
	FILE_TYPE_IGNORE = 'ignore'

	def __init__(self, root_doc_path, out_dir):

		# File queues
		self.files_to_process = deque()
		self.files_aware_of = set()

		# Processors
		self.setup_file_processors()
		self.setup_node_processors()
		self.stats = Counter()

		self.keep_empty_comments = True
		self.out_root_doc_name = 'ms'

		# Paths
		self.root_doc_path = Path(root_doc_path).resolve()
		self.root_dir = self.root_doc_path.parent
		self.out_dir = Path(out_dir).resolve()

		if self.out_dir == self.root_dir:
			raise ValueError('out_dir must be different from root dir')

		if self.out_dir in self.root_dir.parents:
			raise ValueError(f'Root dir must not be inside out dir (root_dir={self.root_dir}, out_dir={self.out_dir}')

		self.all_files = set(self.root_dir.glob('**/*'))

	def setup_file_processors(self):
		self.file_processors = {
			self.FILE_TYPE_TEX: self.process_file_tex,
			self.FILE_TYPE_GRAPHICS: self.process_file_graphics,
			self.FILE_TYPE_OPAQUE: self.process_file_copy,
			self.FILE_TYPE_IGNORE: (lambda path: None),
		}

	def register_node_processor(self, name, func):
		self.node_processors.setdefault(name, []).append(func)

	def setup_node_processors(self):
		self.token_node_processors = [
			self.process_token_remove_comment,
		]
		self.node_processors = {}

		# we will use the names to remove their declarations too
		self.removed_command_names = set()

		self.register_node_processor('input', self.node_input)
		self.register_node_processor('newcommand', self.node_newcommand)
		self.register_node_processor('usepackage', self.node_usepackage)

		for image_cmd in ['includegraphics', 'overpic']:
			self.register_node_processor(image_cmd, self.node_includegraphics)

		self.commands_to_remove('comment')

	def commands_to_remove(self, *commands):
		if commands.__len__() == 1 and isinstance(commands[0], (list, tuple)):
			commands = commands[0]

		for cmd in commands:
			self.removed_command_names.add(cmd)
			self.register_node_processor(cmd, self.node_cmd_remove)

	def commands_to_short_circuit(self, *commands):
		if commands.__len__() == 1 and isinstance(commands[0], (list, tuple)):
			commands = commands[0]

		for cmd in commands:
			self.removed_command_names.add(cmd)
			self.register_node_processor(cmd, self.node_cmd_shortcircuit)

	def additional_files_to_keep(self, *to_keep):
		if to_keep.__len__() == 1 and isinstance(to_keep[0], (list, tuple)):
			to_keep = to_keep[0]

		for path in to_keep:
			path = Path(path)
			path_if_relative = self.root_dir / path
			if path_if_relative.is_file():
				self.add_file_to_process(path_if_relative, self.FILE_TYPE_OPAQUE)
			elif path.is_file():
				self.add_file_to_process(path, self.FILE_TYPE_OPAQUE)
			else:
				raise FileNotFoundError(f'File {path} requested by --keep-file does not exist')

	def clear_out_dir(self):
		if self.out_dir.exists():
			rmtree(self.out_dir)

	def add_file_to_process(self, path, type):
		path = Path(path)

		if path not in self.files_aware_of:
			self.files_aware_of.add(path)
			self.files_to_process.append(self.FileQueueEntry(path, type))

	def run(self):
		# process files recursively starting from root .tex
		self.add_file_to_process(self.root_doc_path, self.FILE_TYPE_TEX)	
		while self.files_to_process:
			queue_entry = self.files_to_process.popleft()
			self.process_file(queue_entry.path, queue_entry.type)

		# rename top level .tex to ms.tex
		root_doc_name = self.root_doc_path.name

		for ext in ['.tex'] + LATEX_COMPILATION_FILES_TO_KEEP:
			rename_from = (self.out_dir / root_doc_name).with_suffix(ext)
			rename_to = (self.out_dir / self.out_root_doc_name).with_suffix(ext)

			if rename_from.is_file() and not rename_to.exists():
				rename_from.rename(rename_to)


	def print_statistics(self):
		print('\n=== Statistics ===\n	' + '\n	'.join(f'{sk}: {sv}' for sk, sv in sorted(self.stats.items())))

	def get_unused_files(self):
		# all_files set in constructor

		unused_files = list(self.all_files.difference(self.files_aware_of))
		unused_files.sort()
		unused_files = [str(f.relative_to(self.root_dir)) for f in unused_files if f.is_file()]

		# remove paths starting with ., like .git or .svn
		unused_files = [f for f in unused_files if not f.startswith('.')]

		return unused_files

	def notify_about_unused_files(self):
		print('\n=== Unused files ===\n	' + '\n	'.join(self.get_unused_files()))


	def out_path(self, for_path):
		return self.out_dir / for_path.relative_to(self.root_dir)

	def process_file(self, path, type):
		proc = self.file_processors.get(type, None)

		if proc:
			proc(path)
		else:
			print(f'No processor for type {type} given to file {path}')

	def process_file_copy(self, path):
		out_file_path = self.out_path(path)
		out_file_path.parent.mkdir(parents=True, exist_ok=True)
		copy_file(path, out_file_path)

	def process_file_graphics(self, path):
		out_file_path = self.out_path(path)
		out_file_path.parent.mkdir(parents=True, exist_ok=True)
		copy_file(path, out_file_path)

	def process_file_tex(self, path):
		self.current_doc_path = path
		self.current_doc_path_relative = self.current_doc_path.relative_to(self.root_dir)
		print('TEX', self.current_doc_path_relative)

		
		with path.open('r') as f_in:
			try:
				doc = TexSoup(f_in)
			except EOFError as e:
				print(f'File {path}: {e}')
		
		# go through the parse tree
		self.process_tex_node(doc, parent_node = None, doc=doc)

		# write processed tex
		out_file_path = self.out_path(path)
		out_file_path.parent.mkdir(parents=True, exist_ok=True)
		out_file_path.write_text(str(doc))

		# ignore trash files resulting from latex compilation
		for trash_ext in LATEX_COMPILATION_FILES_TO_IGNORE:
			trash_file_path = path.with_suffix(trash_ext)
			if trash_file_path.is_file():
				self.add_file_to_process(trash_file_path, self.FILE_TYPE_IGNORE)

		# copy the files from compilation needed by arxiv
		for result_ext in LATEX_COMPILATION_FILES_TO_KEEP:
			result_file_path = path.with_suffix(result_ext)
			if result_file_path.is_file():
				self.add_file_to_process(result_file_path, self.FILE_TYPE_OPAQUE)

	def apply_processors_to_tex_token(self, token, parent_node, doc):
		action = NodeAction.Continue

		for f in self.token_node_processors:
			action = f(token, parent_node=parent_node, doc=doc, cleaner=self)
			
			if action == NodeAction.StopDescent:
				return action

		return action

	def apply_processors_to_tex_node(self, tex_node, parent_node, doc):
		action = NodeAction.Continue

		for f in self.node_processors.get(tex_node.name, []):
			action = f(tex_node, parent_node=parent_node, doc=doc, cleaner=self)

			if action == NodeAction.StopDescent:
				return action

		return action
	
	def process_tex_node(self, tex_node, parent_node=None, doc=None):
		"""
		tex_node can be a full TexSoup.data.TexNode
		or a TexSoup.utils.TokenWithPosition which only has text and position
		"""
			
		if isinstance(tex_node, TokenWithPosition):
			# TokenWithPosition is just a piece of text and does not have children
			self.apply_processors_to_tex_token(tex_node, parent_node, doc)

		#elif isinstance(tex_node, TexNode):
		else:
			if hasattr(tex_node, 'name'):
				action = self.apply_processors_to_tex_node(tex_node, parent_node, doc)
			else:
				action = NodeAction.Continue

			if action != NodeAction.StopDescent:
				for child_node in tex_node.contents:
					self.process_tex_node(child_node, parent_node=tex_node, doc=doc)

		# else:
		# 	print('	Unknown node type:', type(tex_node), 'for node', str(tex_node).split('\n')[0])

	####################################################################################
	# Node processing functions
	####################################################################################

	@staticmethod
	def process_token_remove_comment(tex_token, cleaner, **_):
		if tex_token.text.startswith('%'):
			cleaner.stats['num_inline_comments'] += 1
			tex_token.text = '%' if cleaner.keep_empty_comments else ''
			return NodeAction.StopDescent

	@staticmethod
	def node_cmd_remove(node, cleaner, **_):
		""" This node and its children are not written to the output file """
		node.delete()
		cleaner.stats['num_cmds_removed_' + node.name] += 1
		return NodeAction.StopDescent

	@staticmethod
	def node_cmd_shortcircuit(node, cleaner, **_):
		""" This is replaced with its children """
		if node.args:
			# Only replace when the command has children.
			# if it does not have children, it is being declared with \newcommand
			node.replace_with(*node.contents)
			cleaner.stats['num_cmds_shortcircuited_' + node.name] += 1
		return NodeAction.StopDescent

	@staticmethod
	def node_newcommand(node, cleaner, **_):
		command_being_declared = node.args[0].value.lstrip('\\')

		if command_being_declared in cleaner.removed_command_names:
			node.delete()
			cleaner.stats['num_declarations_removed'] += 1
			return NodeAction.StopDescent

	@staticmethod
	def node_includegraphics(node, cleaner, **_):
		for arg in node.args:
			if arg.type == 'required':
				graphics_path = cleaner.root_dir / arg.value
				if graphics_path.is_file():
					cleaner.add_file_to_process(graphics_path, cleaner.FILE_TYPE_GRAPHICS)
				else:
					print(f'Failed to find graphic {arg.value} included from {cleaner.current_doc_path_relative}')

				return

	@staticmethod
	def find_included_document_path(root_dir, input_link):
		no_ext = root_dir / str(input_link)

		if no_ext.is_file():
			return no_ext

		with_ext = no_ext.with_suffix('.tex')

		if with_ext.is_file():
			return with_ext

		return None

	@staticmethod
	def node_input(node, parent_node, doc, cleaner):
		input_args = list(node.contents)

		if input_args.__len__() != 1:
			print(f'Anomalous \\input, node.args should be length 1: {node}')
		else:
			link_target = str(input_args[0])

			included_document_path = cleaner.find_included_document_path(cleaner.root_dir, link_target)

			if included_document_path:
				cleaner.add_file_to_process(included_document_path, cleaner.FILE_TYPE_TEX)
			else:
				print(f'Failed to find file {link_target} included from {cleaner.current_doc_path_relative}')

	@staticmethod
	def node_usepackage(node, parent_node, doc, cleaner):
		"""
		If `\\usepackage{p}` referfs to an existing `p.sty` file, copy that file.
		"""
		input_args = list(node.contents)

		if input_args.__len__() != 1:
			print(f'Anomalous \\input, node.args should be length 1: {node}')
		else:
			link_target = str(input_args[0])

			package_path = (cleaner.root_dir / link_target).with_suffix('.sty')

			if package_path.is_file():
				cleaner.add_file_to_process(package_path, cleaner.FILE_TYPE_OPAQUE)


####################################################################################
# CLI
####################################################################################


@click.command()
@click.argument(
	'src_root_document', type=click.Path(exists=True, file_okay=True, dir_okay=False))
    #help='Example: my_article/top.tex') # click.Arguments don't have `help`, it is a deficiency of click
@click.argument(
	'dest_dir', type=click.Path())
    #help='Example: my_article_arxiv_submission')
@click.option(
	'--remove-cmd', type=str,
	help='Commands to be removed (\\comment is removed by default). Separate commands with , but not space, for example: comment,KL,WL')
@click.option(
	'--short-circuit-cmd', type=str,
	help='Commands to be replaced with their contents. Separate commands with , but not space, for example: kl,wl')
@click.option(
	'--keep-file', type=click.Path(), multiple=True,
    help='Copy this file to the output directory even if it is not references, repeat the option to add more files')
@click.option(
	'--out-root-doc-name', type=str,
    help='Rename the root doc to this name, by default `ms.tex`')
@click.option(
	'--clear-out-dir', is_flag=True, default=False,
    help='Delete the output directory before outputting')
@click.option(
	'--remove-comments-completely/--keep-empty-comments', is_flag=True, default=False,
	help='Removes the comments including the % (by default the comment body is removed but the % is kept)')
def main(
		src_root_document, dest_dir,
		remove_cmd = None, short_circuit_cmd=None,
		keep_file = [], out_root_doc_name = None,
		clear_out_dir=False, remove_comments_completely=False):
	src_root_document = Path(src_root_document)
	dest_dir = Path(dest_dir)

	cleaner = TexSubmissionCleaner(src_root_document, dest_dir)

	cleaner.keep_empty_comments = not remove_comments_completely

	if clear_out_dir:
		cleaner.clear_out_dir()

	if out_root_doc_name:
		if out_root_doc_name.endswith('.tex'):
			out_root_doc_name = out_root_doc_name[:-4]
		cleaner.out_root_doc_name = out_root_doc_name

	if remove_cmd:
		cmds_to_remove = remove_cmd.split(',')
		print('Removing commands:', ', '.join(cmds_to_remove))
		cleaner.commands_to_remove(*cmds_to_remove)

	if short_circuit_cmd:
		cmds_to_sc = short_circuit_cmd.split(',')
		print('Short-circuiting commands:', ', '.join(cmds_to_sc))
		cleaner.commands_to_remove(*cmds_to_sc)

	cleaner.additional_files_to_keep(keep_file)

	cleaner.run()
	cleaner.notify_about_unused_files()
	cleaner.print_statistics()


if __name__ == '__main__':
	main()
