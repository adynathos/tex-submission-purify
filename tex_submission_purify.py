
from pathlib import Path
from collections import deque
from types import SimpleNamespace
from enum import Enum

from TexSoup import TexSoup
import click

from TexSoup.data import TokenWithPosition, Arg, RArg, TexNode, TexExpr, TexCmd

# def texcmd_patch_remove(self, expr):
# 	# TexCmd uses args and extra instead of _contents
	
# 	if 
	
# 	index = self._contents.index(expr)
# 	self._contents.remove(expr)
# 	return index

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
	We want to remove 

	# (2) 
	# We want to remove or replace TokenWithPosition elements (comments are those), but they don't inherit TexExpr or TexNode.
	"""

	def texcmd_patch_contents(self):
		for arg in self.args:
			yield arg
		if self.extra:
			for expr in self.extra:
				yield expr

	TexCmd.contents = property(texcmd_patch_contents)

patch_TexSoup()

#TexCmd.remove_content = texcmd_patch_remove

class NodeAction(Enum):
	Keep = 0
	Delete = 1
	StopProcessing = 2

class LatexCleaner:
	def __init__(self):
		self.step_limit = 10
		
		self.files_to_process = deque()
		self.files_aware_of = set()
	
		self.setup_file_processors()
		self.setup_node_processors()
	
	def setup_file_processors(self):
		self.file_processors = {
			'.tex': self.process_file_tex,
		}
		
	def setup_node_processors(self):
		self.token_node_processors = [
			self.process_token_remove_comment,
		]
		self.node_processors = {
			
		}
		self.register_node_processor('comment', node_comment)
		self.register_node_processor('input', node_input)
		self.register_node_processor('includegraphics', node_includegraphics)
		
	
	def register_node_processor(self, name, func):
		self.node_processors.setdefault(name, []).append(func)
	
	def add_file_to_process(self, path):
		path = Path(path)
		
		if path not in self.files_aware_of:
			self.files_aware_of.add(path)
			self.files_to_process.append(path)
	
	def run(self, root_doc_path):
		root_doc_path = Path(root_doc_path)
		self.root_dir = root_doc_path.parent
		
		self.files_to_process = deque([root_doc_path])
		
		while self.files_to_process:
			self.process_file(self.files_to_process.popleft())

	def process_file(self, path):
		
		ext = path.suffix.lower()
		
		proc = self.file_processors.get(ext, None)
		
		if proc:
			proc(path)
		else:
			print(f'No processor for file {path}')
		

	def process_file_tex(self, path):
		print('TEX', path)
		self.current_doc_path = path
		
		with path.open('r') as f_in:
			doc = TexSoup(f_in)
		
		# for node in doc.contents:
		# 	self.process_tex_node(node, parent_node=doc, doc=doc)
		
		# go through the parse tree
		self.process_tex_parse_node(doc.expr, parent_node = None, doc=doc)
		
		# for node in doc.contents:
		# 	self.process_tex_node(node, parent_node=doc, doc=doc)
	
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
			
			
# 			except Exception as ex:
# 				print(f'Failed to delete a comment in {self.current_doc_path}, line {doc.char_pos_to_line(node.position)}:', ex)
# 				print(tex_node)
	
	def apply_processors_to_tex_token(self, token, parent_node):
		for f in self.token_node_processors:
			action = f(token, parent_node=parent_node, cleaner=self)
			
			if action in (NodeAction.Delete, NodeAction.StopProcessing):
				return action

		return action

	def apply_processors_to_tex_node(self, tex_node, parent_node):
		for f in self.node_processors.get(tex_node.name.lower(), []):
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
			
			action = self.apply_processors_to_tex_node(tex_node, parent_node)

			if action == NodeAction.Delete:
				self.delete_node(tex_node, parent_node, False)				
			else:
				for child_node in tex_node.contents:
					self.process_tex_node(child_node, tex_node, doc=doc)

		elif isinstance(tex_node, Arg):
			# Children in Arg.exprs, which is a list
	
			for exp in tex_node.exprs:
				self.process_tex_node(exp, parent_node=tex_node, doc=doc)

		elif isinstance(tex_node, TokenWithPosition):
			# TokenWithPosition is just a piece of text and does not have children

			action = self.apply_processors_to_tex_token(tex_node, parent_node)			
			
			if action == NodeAction.Delete:
				self.delete_node(tex_node, parent_node, True)
			
		else:
			print('	Unknown node type:', type(tex_node), 'for node', str(tex_node).split('\n')[0])

	def process_token_remove_comment(self, tex_token, parent_node, **_):
		if tex_token.text.startswith('%'):
			#print('	CM:', tex_token)
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
			cleaner.add_file_to_process(included_document_path)
		else:
			print(f'Failed to find file {link_target} included from {cleaner.current_doc_path}')

def node_comment(node, **_):
	return NodeAction.Delete

def node_includegraphics(node, cleaner, **_):
	for arg in node.args:
		if isinstance(arg, RArg):
			print('Include image:', arg.value)
			cleaner.add_file_to_process(arg.value)

def node_cmd_to_remove(node, **_):
	return NodeAction.Delete

def node_cmd_to_shortcircuit(node, **_):
	node.replace(*node.contents)
	
	return NodeAction.StopProcessing



@click.command
@click.argument('src_root_document', type=click.Path(exists=True, file_okay=True, dir_okay=False))
@click.argument('dest_dir', type=click.Path(), default=None)
def main(src_root_document, dest_dir):
	src_root_document = Path(src_root_document)
	dest_dir = Path(dest_dir)

	c = LatexCleaner()
	c.run(src_root_document)


if __name__ == '__main__':
	main()
