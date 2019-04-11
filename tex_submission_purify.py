
from pathlib import Path
from collections import deque, namedtuple, Counter
from enum import Enum
from shutil import rmtree, copy as copy_file
import os

from TexSoup import TexSoup
import click

from TexSoup.data import TokenWithPosition, Arg, RArg, TexNode, TexEnv, TexExpr, TexCmd

def patch_TexSoup():
	"""
	(1)
	Iterate over contents of Arg
	"""
	def Arg_patch_contents(self):
		for expr in self.exprs:
			if isinstance(expr, (TexEnv, TexCmd)):
				yield TexNode(expr)
			else:
				yield expr

	Arg.contents = property(Arg_patch_contents)


patch_TexSoup()

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
		self.files_to_process = deque()
		self.files_aware_of = set()
	
		self.setup_file_processors()
		self.setup_node_processors()
		self.files_to_process = deque()

		self.stats = Counter()

		self.root_doc_path = Path(root_doc_path).resolve()
		self.root_dir = self.root_doc_path.parent
		self.out_dir = Path(out_dir).resolve()

		self.all_files = set(self.root_dir.glob('**/*'))

		if self.out_dir == self.root_dir:
			raise ValueError('out_dir must be different from root dir')

		if self.out_dir in self.root_dir.parents:
			raise ValueError(f'Root dir must not be inside out dir (root_dir={self.root_dir}, out_dir={self.out_dir}')


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

		self.register_node_processor('comment', node_cmd_remove)
		self.register_node_processor('input', node_input)
		self.register_node_processor('includegraphics', node_includegraphics)

	def commands_to_remove(self, *commands):
		if commands.__len__() == 1 and isinstance(commands[0], (list, tuple)):
			commands = commands[0]

		for cmd in commands:
			self.register_node_processor(cmd, node_cmd_remove)

	def commands_to_short_circuit(self, *commands):
		if commands.__len__() == 1 and isinstance(commands[0], (list, tuple)):
			commands = commands[0]

		for cmd in commands:
			self.register_node_processor(cmd, node_cmd_shortcircuit)

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
			rename_to = (self.out_dir / 'ms').with_suffix(ext)

			if rename_from.is_file() and not rename_to.exists():
				rename_from.rename(rename_to)


	def print_statistics(self):
		print('\n=== Statistics ===\n	' + '\n	'.join(f'{sk}: {sv}' for sk, sv in self.stats.items()))

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
		
	def process_file_ignore(self, path):
		pass

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
			doc = TexSoup(f_in)
		
		# for node in doc.contents:
		# 	self.process_tex_node(node, parent_node=doc, doc=doc)
		
		# go through the parse tree
		self.process_tex_node(doc, parent_node = None, doc=doc)
		
		# for node in doc.contents:
		# 	self.process_tex_node(node, parent_node=doc, doc=doc)

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
			action = f(token, parent_node=parent_node, cleaner=self)
			
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
			action = self.apply_processors_to_tex_token(tex_node, parent_node, doc)

		elif isinstance(tex_node, TexNode):
			action = self.apply_processors_to_tex_node(tex_node, parent_node, doc)

			if action != NodeAction.StopDescent:
				for child_node in tex_node.contents:
					self.process_tex_node(child_node, parent_node=tex_node, doc=doc)

		elif isinstance(tex_node, Arg):
			for expr in tex_node.exprs:
				if isinstance(expr, (TexEnv, TexCmd)):
					child_node = TexNode(expr)
				else:
					child_node = expr
			
				self.process_tex_node(child_node, parent_node=tex_node, doc=doc)
			
		else:
			print('	Unknown node type:', type(tex_node), 'for node', str(tex_node).split('\n')[0])

	def process_token_remove_comment(self, tex_token, parent_node, **_):
		if tex_token.text.startswith('%'):
			tex_token.__str__ = str_method_deleted_node

			self.stats['num_inline_comments'] += 1
			
			return NodeAction.StopDescent


####################################################################################
# Node processing functions
####################################################################################


def str_method_deleted_node(self):
	""" This node and its children are not written to the output file """
	return ''

def node_cmd_remove(node, cleaner, **_):
	""" This node and its children are not written to the output file """
	node.__str__ = str_method_deleted_node

	cleaner.stats['num_cmds_removed_' + node.name] += 1

	return NodeAction.StopDescent

def str_method_short_circuited_node(self):
	""" This is replaced with its children """
	return ' '.join(map(str, self.contents))

def node_cmd_shortcircuit(node, cleaner, **_):
	""" This is replaced with its children """
	node.__str__ = str_method_short_circuited_node

	cleaner.stats['num_cmds_shortcircuited_' + node.name] += 1

	return NodeAction.StopDescent


def find_included_document_path(root_dir, input_link):
	no_ext = root_dir / str(input_link)
	
	if no_ext.is_file():
		return no_ext
	
	with_ext = no_ext.with_suffix('.tex')
	
	if with_ext.is_file():
		return with_ext
	
	return with_ext
			
def node_input(node, parent_node, doc, cleaner):
	
	input_args = list(node.contents)

	if input_args.__len__() != 1:
		print(f'Anomalous \\input, node.args should be length 1: {node}')
	else:
		link_target = str(input_args[0])

		# try:
		# except Exception as e:
		# 	print('Input arg fail')
		# 	print('Arg[0] is', input_args[0])
		# 	print('Node is ', node)
		# 	cleaner.node_to_inspect = node

		included_document_path = find_included_document_path(cleaner.root_dir, link_target)

		if included_document_path:
			cleaner.add_file_to_process(included_document_path, cleaner.FILE_TYPE_TEX)
		else:
			print(f'Failed to find file {link_target} included from {cleaner.current_doc_path_relative}')

def node_includegraphics(node, cleaner, **_):
	for arg in node.args:
		if isinstance(arg, RArg):
			graphics_path = cleaner.root_dir / arg.value
			if graphics_path.is_file():
				cleaner.add_file_to_process(graphics_path, cleaner.FILE_TYPE_GRAPHICS)
			else:
				print(f'Failed to find graphic {arg.value} included from {cleaner.current_doc_path_relative}')

####################################################################################
# CLI interface
####################################################################################

@click.command
@click.argument('src_root_document', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.argument('dest_dir', type=click.Path())
@click.option('--remove-cmd', type=str, help='Separate commands with , but not space, for example: comment,KL,WL')
@click.option('--short-circuit-cmd', type=str, help='These commands are replaced with their contents. Separate commands with , but not space, for example: kl,wl')
@click.option('--keep-file', type=click.Path(), multiple=True)
@click.option('--clear-out-dir', is_flag=True, default=False)
def main(src_root_document, dest_dir, remove_cmd = None, short_circuit_cmd=None, keep_file = [], clear_out_dir=False):
	src_root_document = Path(src_root_document)
	dest_dir = Path(dest_dir)

	cleaner = TexSubmissionCleaner(src_root_document, dest_dir)

	if clear_out_dir:
		cleaner.clear_out_dir()

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
