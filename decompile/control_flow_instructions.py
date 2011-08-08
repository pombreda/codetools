'''
Created on Jul 14, 2011

@author: sean
'''

from opcode import *
import _ast
from decompile.disassemble import Instruction
AND_JUMPS = ['JUMP_IF_FALSE_OR_POP', 'POP_JUMP_IF_FALSE']
OR_JUMPS = ['JUMP_IF_TRUE_OR_POP', 'POP_JUMP_IF_TRUE']
JUMPS = AND_JUMPS + OR_JUMPS
JUMP_OPS = [opmap[name] for name in JUMPS]


def split(block, name):
    func = lambda instr: instr.opname == name
    return split_cond(block, func)

def split_cond(block, func, raise_=True):
    block = block[:]

    new_block = []
    while len(block):
        instr = block.pop(0)
        if func(instr):
            return new_block, instr, block
        new_block.append(instr)

    if raise_:
        raise Exception("function found no matching instruction")

    return new_block, None, block

def find_index(lst, func, default=None):
    for i, item in enumerate(lst):
        if func(item):
            return i
    else:
        return default

def rfind_index(lst, func, default=None):
    for i, item in reversed(list(enumerate(lst))):
        if func(item):
            return i
    else:
        return default

def refactor_ifs(stmnt, ifs):
    '''
    for if statements in list comprehension
    '''
    if isinstance(stmnt, _ast.BoolOp):
        test, right = stmnt.values
        if isinstance(stmnt.op, _ast.Or):
            test = _ast.UnaryOp(op=_ast.Not(), operand=test, lineno=0, col_offset=0)

        ifs.append(test)

        return refactor_ifs(right, ifs)
    return stmnt

def parse_logic(struct):

    lineno = struct.lineno

    kw = dict(lineno=lineno, col_offset=0)
    if isinstance(struct.right, LogicalOp):
        ast_right, insert_into = parse_logic(struct.right)
        assert insert_into is None
    else:
        ast_right = struct.right

    parent = struct.parent

    Logic = _ast.Or if struct.flag == 'OR' else _ast.And

    if isinstance(parent, LogicalOp):
        ast_parent, insert_into = parse_logic(struct.parent)

        new_insert_into = [ast_right]
        insert_into.insert(0, _ast.BoolOp(op=Logic(), values=new_insert_into, **kw))
        return ast_parent, new_insert_into

    elif parent is None:
        insert_into = [ast_right]
        return _ast.BoolOp(op=Logic(), values=insert_into, **kw), insert_into

    else:
        bool_op = _ast.BoolOp(op=Logic(), values=[parent, ast_right], **kw)
        return bool_op, None


class ListCompTmp(object):
    def __init__(self, assign, list, ifs, lineno):
        self.assign = assign.nodes[0]
        self.list = list
        self.ifs = ifs
        self.lineno = lineno

class LogicalOp(object):
    def __init__(self, flag, right, parent, lineno):
        self.flag = flag
        self.right = right
        self.parent = parent
        self.lineno = lineno

    def __repr__(self):
        return '%s(%r, parent=%r)' % (self.flag, self.right, self.parent)


class CtrlFlowInstructions(object):

    def split_handlers(self, handlers_blocks):

        handlers = []
        except_instrs = []

        ends = []
        while len(handlers_blocks):

            instr = handlers_blocks.pop(0)
            except_instrs.append(instr)

            if (opname[instr.op] == 'COMPARE_OP') and (instr.arg == 'exception match'):

                jump = handlers_blocks.pop(0)
                assert jump.opname == 'POP_JUMP_IF_FALSE'

                next_handler = jump.oparg

                instr = handlers_blocks.pop(0)
                except_instrs.append(instr)
                instr = handlers_blocks.pop(0)
                except_instrs.append(instr)
                instr = handlers_blocks.pop(0)
                except_instrs.append(instr)

                assert except_instrs[0].opname == 'DUP_TOP'
                assert except_instrs[-3].opname == 'POP_TOP'
                assert except_instrs[-1].opname == 'POP_TOP'

                exec_stmnt = self.decompile_block(except_instrs[1:-4]).stmnt()

                assert len(exec_stmnt) == 1

                exc_type = exec_stmnt[0]


                if opname[except_instrs[-2].op] == 'STORE_NAME':
                    exc_name = _ast.Name(id=except_instrs[-2].arg, ctx=_ast.Store(), lineno=except_instrs[-2].lineno, col_offset=0)
                else:
                    assert opname[except_instrs[-2].op] == 'POP_TOP'
                    exc_name = None

                handler_body = []
                while len(handlers_blocks):
                    instr = handlers_blocks.pop(0)
                    if instr.i == next_handler:
                        handlers_blocks.insert(0, instr)
                        break

                    handler_body.append(instr)

                assert opname[handler_body[-1].op] == 'JUMP_FORWARD'
                ends.append(handler_body[-1].arg)

                exc_body = self.decompile_block(handler_body[:-1]).stmnt()

                handlers.append(_ast.ExceptHandler(type=exc_type, name=exc_name, body=exc_body, lineno=instr.lineno, col_offset=0))

                except_instrs = []

        assert opname[except_instrs[-1].op] == 'END_FINALLY'

        if len(except_instrs) == 1:
            pass
        else:

            assert opname[except_instrs[0].op] == 'POP_TOP'
            assert opname[except_instrs[1].op] == 'POP_TOP'
            assert opname[except_instrs[2].op] == 'POP_TOP'
            assert opname[except_instrs[-2].op] == 'JUMP_FORWARD'
            ends.append(except_instrs[-2].arg)
            exc_body = self.decompile_block(except_instrs[3:-2]).stmnt()
            handlers.append(_ast.ExceptHandler(type=None, name=None, body=exc_body, lineno=except_instrs[0].lineno, col_offset=0))

            assert all(e == ends[0] for e in ends)

        end = ends[0]

        return end, handlers

#
#
#    def SETUP_FINALLY(self, instr):
#        raise Exception

    def SETUP_EXCEPT(self, instr):

        to = instr.arg

        try_block = self.make_block(to, inclusive=False)

#        if opname[try_block[-1].op] == 'DUP_TOP':
#            try_block.pop()
        assert opname[try_block[-1].op] == 'JUMP_FORWARD'
        assert opname[try_block[-2].op] == 'POP_BLOCK'

        try_stmnts = self.decompile_block(try_block[:-2]).stmnt()
        body = try_stmnts

        handlers_blocks = self.make_block(try_block[-1].arg, inclusive=False)

        end, handlers = self.split_handlers(handlers_blocks)

        else_block = self.make_block(end, inclusive=False)
        else_stmnts = self.decompile_block(else_block).stmnt()
        if else_stmnts:
            else_ = else_stmnts
        else:
            else_ = []

        try_except = _ast.TryExcept(body=body, handlers=handlers, orelse=else_, lineno=instr.lineno, col_offset=0)

        self.ast_stack.append(try_except)

    def SETUP_LOOP(self, instr):
        to = instr.arg
        loop_block = self.make_block(to, inclusive=False)

        if 'FOR_ITER' in [opname[ins.op] for ins in loop_block]:
            self.for_loop(loop_block)
        else:
            self.while_loop(instr, loop_block)

    def BREAK_LOOP(self, instr):
        self.ast_stack.append(_ast.Break(lineno=instr.lineno, col_offset=0))

    def for_loop(self, loop_block):

        iter_block, _, body_else_block = split(loop_block, 'GET_ITER')

#        for_iter = body_else_block[0]
        for_iter = body_else_block.pop(0)

        assert for_iter.opname == 'FOR_ITER'

        idx = find_index(body_else_block, lambda instr: instr.opname == 'POP_BLOCK' and for_iter.to == instr.i)

        assert idx is not False

        body_block = body_else_block[:idx]

        else_block = body_else_block[idx + 1:]

        jump_abs = body_block.pop()

        assert jump_abs.opname == 'JUMP_ABSOLUTE' and jump_abs.to == for_iter.i

        iter_stmnt = self.decompile_block(iter_block).stmnt()

        assert len(iter_stmnt) == 1
        iter_stmnt = iter_stmnt[0]

        body_lst = self.decompile_block(body_block[:], stack_items=[None], jump_map={for_iter.i:for_iter.to}).stmnt()

        assign_ = body_lst.pop(0)
        body = body_lst

        if len(else_block) == 0:
            else_ = []
        else:
            else_ = self.decompile_block(else_block[:]).stmnt()

        assign = assign_.targets[0]
        for_ = _ast.For(target=assign, iter=iter_stmnt, body=body, orelse=else_, lineno=iter_stmnt.lineno, col_offset=0)

        self.ast_stack.append(for_)

    def make_list_comp(self, get_iter, for_iter):

        block = self.make_block(for_iter.to, inclusive=False, raise_=True)

        jump_abs = block.pop()

        assert jump_abs.opname == 'JUMP_ABSOLUTE'

        stmnts = self.decompile_block(block, stack_items=[None], jump_map={for_iter.i:for_iter.to}).stmnt()

        assert len(stmnts) > 1

        assign = stmnts.pop(0)

        assert len(stmnts) == 1

        assert isinstance(assign, _ast.Assign)


        list_expr = self.ast_stack.pop()
        # empty ast.List object
        list_ = self.ast_stack.pop()

        ifs = []
        list = refactor_ifs(stmnts[0], ifs)

        assert len(assign.targets) == 1
        quals = [_ast.comprehension(target=assign.targets[0], iter=list_expr, ifs=ifs, lineno=get_iter.lineno, col_offset=0)]

        list_comp = _ast.ListComp(elt=list, generators=quals, lineno=get_iter.lineno, col_offset=0)

        self.ast_stack.append(list_comp)

    def extract_setcomp(self, function, sequence):

        assert len(function.code.nodes) == 1
        assert isinstance(function.code.nodes[0], _ast.Return)

        value = function.code.nodes[0].value

        assert isinstance(value, _ast.ListComp)

        quals = value.quals
        expr = value.expr

        for qual in quals:
            qual.list = sequence

        setcomp = _ast.SetComp(elt=expr, generators=quals, lineno=value.lineno, col_offset=0)
        self.ast_stack.append(setcomp)

    def extract_dictcomp(self, function, sequence):

        assert len(function.code.nodes) == 1
        assert isinstance(function.code.nodes[0], _ast.Return)

        value = function.code.nodes[0].value

        assert isinstance(value, _ast.ListComp)

        quals = value.quals
        key, value = value.expr

        for qual in quals:
            qual.list = sequence

        setcomp = _ast.DictComp(key=key, value=value, generators=quals, lineno=value.lineno, col_offset=0)
        self.ast_stack.append(setcomp)

    def GET_ITER(self, instr):

        for_iter = self.ilst.pop(0)

        if for_iter.opname == 'CALL_FUNCTION':
            call_function = for_iter
            assert call_function.oparg == 1

            sequence = self.ast_stack.pop()
            function = self.ast_stack.pop()

            if function.name == '<setcomp>':
                self.extract_setcomp(function, sequence)
            elif function.name == '<dictcomp>':
                self.extract_dictcomp(function, sequence)
            else:
                assert False, function.name

        elif for_iter.opname == 'FOR_ITER':
            self.make_list_comp(instr, for_iter)
        else:
            assert False


    def LIST_APPEND(self, instr):

        assert instr.oparg == 2

    def MAP_ADD(self, instr):
        key = self.ast_stack.pop()
        value = self.ast_stack.pop()

        self.ast_stack.append((key, value))
        'NOP'

    def SET_ADD(self, instr):
        'NOP'

    def FOR_ITER(self, instr):
        #set or dict comp
        self.make_list_comp(instr, instr)

    def while_loop(self, instr, loop_block):

        kw = dict(lineno=instr.lineno, col_offset=0)

        loop_block_map = {instr.i:instr.op for instr in loop_block}

        #Find the last conditional that will exit the loop
        func = lambda instr: opname[instr.op] == 'POP_JUMP_IF_FALSE' and (instr.oparg in loop_block_map) and opname[loop_block_map[instr.oparg]] == 'POP_BLOCK'

        idx = rfind_index(loop_block, func)


        # in python this would be while 1:
        if idx is None:
            body_block = loop_block
            body_ = self.decompile_block(body_block[:-1]).stmnt()
            while_ = _ast.While(test=_ast.Num(1, **kw), body=body_, orelse=[], **kw)
            self.ast_stack.append(while_)
            return

        cond_block = loop_block[:idx]
        instr = loop_block[idx]
        body_block = loop_block[idx + 1:]

        first_cond_ = cond_block[0]
        iter_stmnt = self.decompile_block(cond_block).stmnt()

        assert len(iter_stmnt) == 1

        iter = iter_stmnt[0]

        body_block, _POP_BLOCK, else_block = split_cond(body_block, func=lambda new_instr: instr.oparg == new_instr.i)

        assert opname[_POP_BLOCK.op] == 'POP_BLOCK'

        assert opname[body_block[-1].op] == 'JUMP_ABSOLUTE' and body_block[-1].oparg == first_cond_.i
        body_ = self.decompile_block(body_block[:-1]).stmnt()

        if else_block[:-1]:
            else_ = self.decompile_block(else_block[:]).stmnt()
        else:
            else_ = []

        while_ = _ast.While(test=iter, body=body_, orelse=else_, **kw)

        self.ast_stack.append(while_)


    def gather_jumps(self, jump_instr):

        to = self.jump_map.get(jump_instr.to, jump_instr.to)
#        if jump_instr.to in self.jump_map:
        assert to > jump_instr.i


        and_block = self.make_block(to=to, inclusive=False, raise_=False)

        jump_tos = {to}
        last_len = 0
        old_max = to

        while len(jump_tos) != last_len:
            last_len = len(jump_tos)

            for instr in and_block:
                if instr.opname in JUMPS:
                    to = self.jump_map.get(instr.to, instr.to)
                    assert to > jump_instr.i
                    jump_tos.add(to)

            if old_max < max(jump_tos):
                old_max = max(jump_tos)
                new_block = self.make_block(to=old_max, inclusive=False, raise_=False)
                and_block.extend(new_block)

        return and_block

    def process_logic(self, logic_block):

        if opname[logic_block[0].op] in JUMPS:
            jump_instr = logic_block[0]
            flag = 'OR' if opname[jump_instr.op] in OR_JUMPS else 'AND'
            idx = find_index(logic_block, lambda instr: jump_instr.oparg == instr.i, default=None)

            if idx is None:
                if len(logic_block) == 1:
                    right = None
                else:
                    right = self.process_logic(logic_block[1:])
                parent = None
#                assert False
            else:
                right = self.process_logic(logic_block[1:idx - 1])
                parent = self.process_logic(logic_block[idx - 1:])

#            if right is None:
            return LogicalOp(flag, right, parent, jump_instr.lineno)
        else:
            idx = find_index(logic_block, lambda instr: opname[instr.op] in JUMPS, default=None)

            if idx is None:
                stmnts = self.decompile_block(logic_block).stmnt()
                assert len(stmnts) == 1
                return stmnts[0]
            else:
                right = logic_block[idx:]
                parent = logic_block[:idx]

                stmnts = self.decompile_block(parent).stmnt()
                assert len(stmnts) == 1
                parent = stmnts[0]

                right = self.process_logic(right)

                assert right.parent is None

                if right.right is None:
                    return parent

                right.parent = parent
                return right


    def logic_ast(self, instr, left, hi):
#        flag = 'OR' if opname[instr.op] in OR_JUMPS else 'AND'

        ast_, insert_into = parse_logic(hi)

        insert_into.insert(0, left)

        return ast_

    def JUMP_IF_TRUE_OR_POP(self, instr):
        left = self.ast_stack.pop()

        and_block = self.gather_jumps(instr)
        hi = self.process_logic([instr] + and_block)
        ast_ = self.logic_ast(instr, left, hi)
        self.ast_stack.append(ast_)

    def make_if(self, instr, left, and_block):

        block = [instr] + and_block[:-1]

        maxmax = max(block, key=lambda ins: (0, 0) if (ins.op not in JUMP_OPS) else (ins.oparg, ins.i))

        idx = block.index(maxmax)

        assert idx is not None

        hi = self.process_logic(block[:idx + 1])
        if hi.right is None and hi.parent is None:
            if instr.opname == 'POP_JUMP_IF_TRUE':
                cond = _ast.UnaryOp(op=_ast.Not(), operand=left, lineno=0, col_offset=0)
#                cond = ast.Not(left, lineno=instr.lineno)
            else:
                cond = left

        else:
            cond = self.logic_ast(instr, left, hi)

#        if block[-1].opname == 'JUMP_ABSOLUTE':
#            pass

        body = self.decompile_block(block[idx + 1:]).stmnt()

#        tests = [(cond, body)]

        jump = and_block[-1]
        else_block = self.make_block(jump.to, inclusive=False, raise_=False)

        if len(else_block):
            else_ = self.decompile_block(else_block).stmnt()
#
#            if len(else_lst) == 1 and isinstance(else_lst[0], _ast.If):
#                elif_ = else_lst[0]
#                tests.extend(elif_.tests)
#                else_ = elif_.else_
#            else:
#                else_ = else_lst
        else:
            else_ = []

        if_ = _ast.If(test=cond, body=body, orelse=else_, lineno=instr.lineno, col_offset=0)

        self.ast_stack.append(if_)


    def POP_JUMP_IF_TRUE(self, instr):

        left = self.ast_stack.pop()

        and_block = self.gather_jumps(instr)

        if and_block[-1].opname in ['JUMP_FORWARD', 'JUMP_ABSOLUTE']:

            self.make_if(instr, left, and_block)
            return
        else:
            hi = self.process_logic([instr] + and_block)
            ast_ = self.logic_ast(instr, left, hi)
            self.ast_stack.append(ast_)

    def POP_JUMP_IF_FALSE(self, instr):
        left = self.ast_stack.pop()

        and_block = self.gather_jumps(instr)

        if and_block[-1].opname in ['JUMP_FORWARD', 'JUMP_ABSOLUTE', 'RETURN_VALUE']:
            
            #this happens if the function was going to return anyway
            if and_block[-1].opname == 'RETURN_VALUE':
                JUMP_FORWARD = Instruction(and_block[-1].i, 110, lineno=0)
                JUMP_FORWARD.arg = instr.to
                and_block.append(JUMP_FORWARD)
                
            self.make_if(instr, left, and_block)
            return
        else:
            hi = self.process_logic([instr] + and_block)
            ast_ = self.logic_ast(instr, left, hi)
            self.ast_stack.append(ast_)

    def JUMP_IF_FALSE_OR_POP(self, instr):
        left = self.ast_stack.pop()

        and_block = self.gather_jumps(instr)
        hi = self.process_logic([instr] + and_block)
        ast_ = self.logic_ast(instr, left, hi)
        self.ast_stack.append(ast_)

    def JUMP_ABSOLUTE(self, instr):
        continue_ = _ast.Continue(lineno=instr.lineno, col_offset=0)
        self.ast_stack.append(continue_)

    def JUMP_FORWARD(self, instr):
        pass

    def SETUP_WITH(self, instr):

        with_block = self.make_block(to=instr.to, inclusive=False)

        assert with_block.pop().opname == 'LOAD_CONST'
        assert with_block.pop().opname == 'POP_BLOCK'

        with_cleanup = self.ilst.pop(0)
        assert with_cleanup.opname == 'WITH_CLEANUP'
        end_finally = self.ilst.pop(0)
        assert end_finally.opname == 'END_FINALLY'


        with_ = self.decompile_block(with_block, stack_items=['WITH_BLOCK']).stmnt()

        if isinstance(with_[0], _ast.Assign) and with_[0].expr == 'WITH_BLOCK':
            assign = with_.pop(0)
            as_ = assign.nodes[0]

        body = with_

        expr = self.ast_stack.pop()

        with_ = _ast.With(context_expr=expr, optional_vars=as_, body=body,
                          lineno=instr.lineno, col_offset=0)

        self.ast_stack.append(with_)