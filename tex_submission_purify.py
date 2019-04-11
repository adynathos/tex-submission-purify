
from pathlib import Path
from collections import deque, namedtuple
from enum import Enum
from shutil import rmtree, copy as copy_file
import os

from TexSoup import TexSoup
import click

from TexSoup.data import TokenWithPosition, Arg, RArg, TexNode, TexEnv, TexExpr, TexCmd

def patch_TexSoup():
	"""
	We alter the TexSoup library to ensure it is possible to delete parse-tree nodes.
	A `TexCmd` node contains `Arg` child elements.
	
	(1)
	By default, the `TexCmd.contents` method iterates not over those `Arg`s but over their children 
	(skipping one level in the parse tree).
	But to delete a node, we need to know its parent node - we need to delete from the `Arg`s not from `TexCmd`.

	We alter `TexCmd.contents` to output the `Arg`s not their children, preserving the hierarchy.

	(2)
	Iterate over contents of Arg
	"""

	def TexCmd_patch_contents(self):
		for arg in self.args:
			yield arg
		if self.extra:
			for expr in self.extra:
				if isinstance(expr, (TexEnv, TexCmd)):
					yield TexNode(expr)
				else:
					yield expr

	TexCmd.contents = property(TexCmd_patch_contents)


	def Arg_patch_contents(self):
		for expr in self.exprs:
			if isinstance(expr, (TexEnv, TexCmd)):
				yield TexNode(expr)
			else:
				yield expr

	Arg.contents = property(Arg_patch_contents)


patch_TexSoup()

#TexCmd.remove_content = texcmd_patch_remove

class NodeAction(Enum):
	Keep = 0
	Delete = 1
	#Replace = 2
	StopProcessing = 3

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

		self.stats = {
			'num_cmds_removed': 0,
			'num_inline_comments': 0,
		}


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

		self.register_node_processor('comment', node_cmd_to_remove)
		self.register_node_processor('input', node_input)
		self.register_node_processor('includegraphics', node_includegraphics)

	def commands_to_remove(self, *commands):
		if commands.__len__() == 1 and isinstance(commands[0], (list, tuple)):
			commands = commands[0]

		for cmd in commands:
			self.register_node_processor(cmd, node_cmd_to_remove)

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
		self.out_root_doc_path = self.out_dir / 'ms.tex'


		self.add_file_to_process(self.root_doc_path, self.FILE_TYPE_TEX)
		
		while self.files_to_process:
			queue_entry = self.files_to_process.popleft()
			self.process_file(queue_entry.path, queue_entry.type)

		self.notify_about_unused_files()

		print('\n=== Statistics ===\n	' + '\n	'.join(f'{sk}: {sv}' for sk, sv in self.stats.items()))

	def notify_about_unused_files(self):

		# all_files set in constructor

		unused_files = list(self.all_files.difference(self.files_aware_of))
		unused_files.sort()
		unused_files = [str(f.relative_to(self.root_dir)) for f in unused_files if f.is_file()]

		# remove paths starting with ., like .git or .svn
		unused_files = [f for f in unused_files if not f.startswith('.')]

		print('\n=== Unused files ===\n	' + '\n	'.join(unused_files))


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


	def delete_node(self, node, parent, is_token):
		
		to_remove = node if is_token else node.expr
		
		if isinstance(parent, TexNode):
			try:
				parent.expr.remove_content(to_remove)
			except Exception as e:
				print(' --- ', self.current_doc_path)
				print('Trying to remove', node)
				print('Parent', type(parent), parent)
				self.to_inspect_node = node
				self.to_inspect_parent = parent
				raise e
				
		elif isinstance(parent, TexExpr):
			parent.remove_content(to_remove)

		elif isinstance(parent, Arg):
			if isinstance(parent.exprs, tuple):
				parent.exprs = list(parent.exprs)
			
			parent.exprs.remove(to_remove)

	# def replace_node(self, node, parent, is_token, new_node):
	# 	to_remove = node if is_token else node.expr
	#
	# 	if isinstance(parent, TexNode):
	# 		try:
	# 			parent.expr.add_contents_at(
	# 				parent.expr.remove_content(to_remove),
	# 				new_node,
	# 			)
	#
	# 		except Exception as e:
	# 			print(' --- ', self.current_doc_path)
	# 			print('Trying to replace', node)
	# 			print('Parent', type(parent), parent)
	# 			self.to_inspect_node = node
	# 			self.to_inspect_parent = parent
	# 			raise e
	#
	# 	elif isinstance(parent, TexExpr):
	# 		parent.add_contents_at(
	# 			parent.remove_content(to_remove),
	# 			new_node,
	# 		)
	#
	# 	elif isinstance(parent, Arg):
	# 		if isinstance(parent.exprs, tuple):
	# 			parent.exprs = list(parent.exprs)
	#
	# 		index = parent.exprs.index(to_remove)
	# 		parent.exprs[index] = new_node


	def apply_processors_to_tex_token(self, token, parent_node, doc):
		action = NodeAction.Keep

		for f in self.token_node_processors:
			action = f(token, parent_node=parent_node, cleaner=self)
			
			if action in (NodeAction.Delete, NodeAction.StopProcessing):
				return action

		return action

	def apply_processors_to_tex_node(self, tex_node, parent_node, doc):
		action = NodeAction.Keep

		for f in self.node_processors.get(tex_node.name, []):
			action = f(tex_node, parent_node=parent_node, doc=doc, cleaner=self)

			if action in (NodeAction.Delete, NodeAction.StopProcessing):
				return action

		return action
	
	def process_tex_node(self, tex_node, parent_node=None, doc=None):
		"""
		tex_node can be a full TexSoup.data.TexNode
		or a TexSoup.utils.TokenWithPosition which only has text and position
		"""
			
		if isinstance(tex_node, TexNode):
			#print('NODE', tex_node.expr)
			
			action = self.apply_processors_to_tex_node(tex_node, parent_node, doc)

			if action == NodeAction.Delete:
				self.delete_node(tex_node, parent_node, False)				
			else:
				for child_node in tex_node.contents:
					self.process_tex_node(child_node, parent_node=tex_node, doc=doc)

		elif isinstance(tex_node, Arg):
			# Children in Arg.exprs, which is a list
	
			for exp in tex_node.contents:
				self.process_tex_node(exp, parent_node=tex_node, doc=doc)

		elif isinstance(tex_node, TokenWithPosition):
			# TokenWithPosition is just a piece of text and does not have children

			action = self.apply_processors_to_tex_token(tex_node, parent_node, doc)
			
			if action == NodeAction.Delete:
				self.delete_node(tex_node, parent_node, True)

		else:
			print('	Unknown node type:', type(tex_node), 'for node', str(tex_node).split('\n')[0])

	def process_token_remove_comment(self, tex_token, parent_node, **_):
		if tex_token.text.startswith('%'):
			#print('	CM:', tex_token)
			self.stats['num_inline_comments'] += 1
			return NodeAction.Delete

			
def find_included_document_path(root_dir, input_link):
	no_ext = root_dir / str(input_link)
	
# 	print(f'{parent_path} -> {input_link}')
	
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
		link_target = input_args[0].value

		included_document_path = find_included_document_path(cleaner.root_dir, link_target)

		if included_document_path:
			cleaner.add_file_to_process(included_document_path, cleaner.FILE_TYPE_TEX)
		else:
			print(f'Failed to find file {link_target} included from {cleaner.current_doc_path_relative}')

def node_includegraphics(node, cleaner, **_):
	for arg in node.args:
		if isinstance(arg, RArg):
			#print('Include image:', arg.value)

			graphics_path = cleaner.root_dir / arg.value
			if graphics_path.is_file():
				cleaner.add_file_to_process(graphics_path, cleaner.FILE_TYPE_GRAPHICS)
			else:
				print(f'Failed to find graphic {arg.value} included from {cleaner.current_doc_path_relative}')

def node_cmd_to_remove(node, cleaner, **_):
	cleaner.stats['num_cmds_removed'] += 1

	k = 'num_cmds_removed_' + node.name
	cleaner.stats.setdefault(k, 0)
	cleaner.stats[k] += 1

	return NodeAction.Delete

# def node_cmd_to_shortcircuit(node, cleaner, **_):
# 	cleaner.stats['num_cmds_shortcircuited'] += 1
# 	node.replace(*node.contents)
# 	return NodeAction.StopProcessing


@click.command
@click.argument('src_root_document', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.argument('dest_dir', type=click.Path())
@click.option('--remove-cmd', type=str, help='Separate commands with , but not space, for example: comment,KL,WL')
@click.option('--keep-file', type=click.Path(), multiple=True)
@click.option('--clear-out-dir', is_flag=True)
def main(src_root_document, dest_dir, remove_cmd = None, keep_file = [], clear_out_dir=False):
	src_root_document = Path(src_root_document)
	dest_dir = Path(dest_dir)

	c = TexSubmissionCleaner(src_root_document, dest_dir)

	if clear_out_dir:
		c.clear_out_dir()

	if remove_cmd:
		c.commands_to_remove(*remove_cmd.split(','))

	c.additional_files_to_keep(keep_file)

	c.run()


if __name__ == '__main__':
	main()
