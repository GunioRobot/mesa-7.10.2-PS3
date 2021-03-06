#!/usr/bin/env python

# Mesa 3-D graphics library
# Version:  7.9
#
# Copyright (C) 2010 LunarG Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
#
# Authors:
#    Chia-I Wu <olv@lunarg.com>

import sys
import re
from optparse import OptionParser

# number of dynamic entries
ABI_NUM_DYNAMIC_ENTRIES = 256

class ABIEntry(object):
    """Represent an ABI entry."""

    _match_c_param = re.compile(
            '^(?P<type>[\w\s*]+?)(?P<name>\w+)(\[(?P<array>\d+)\])?$')

    def __init__(self, cols, attrs):
        self._parse(cols)

        self.slot = attrs['slot']
        self.hidden = attrs['hidden']
        self.alias = attrs['alias']

    def c_prototype(self):
        return '%s %s(%s)' % (self.c_return(), self.name, self.c_params())

    def c_return(self):
        ret = self.ret
        if not ret:
            ret = 'void'

        return ret

    def c_params(self):
        """Return the parameter list used in the entry prototype."""
        c_params = []
        for t, n, a in self.params:
            sep = '' if t.endswith('*') else ' '
            arr = '[%d]' % a if a else ''
            c_params.append(t + sep + n + arr)
        if not c_params:
            c_params.append('void')

        return ", ".join(c_params)

    def c_args(self):
        """Return the argument list used in the entry invocation."""
        c_args = []
        for t, n, a in self.params:
            c_args.append(n)

        return ", ".join(c_args)

    def _parse(self, cols):
        ret = cols.pop(0)
        if ret == 'void':
            ret = None

        name = cols.pop(0)

        params = []
        if not cols:
            raise Exception(cols)
        elif len(cols) == 1 and cols[0] == 'void':
            pass
        else:
            for val in cols:
                params.append(self._parse_param(val))

        self.ret = ret
        self.name = name
        self.params = params

    def _parse_param(self, c_param):
        m = self._match_c_param.match(c_param)
        if not m:
            raise Exception('unrecognized param ' + c_param)

        c_type = m.group('type').strip()
        c_name = m.group('name')
        c_array = m.group('array')
        c_array = int(c_array) if c_array else 0

        return (c_type, c_name, c_array)

    def __str__(self):
        return self.c_prototype()

    def __cmp__(self, other):
        # compare slot, alias, and then name
        res = cmp(self.slot, other.slot)
        if not res:
            if not self.alias:
                res = -1
            elif not other.alias:
                res = 1

            if not res:
                res = cmp(self.name, other.name)

        return res

def abi_parse_line(line):
    cols = [col.strip() for col in line.split(',')]

    attrs = {
            'slot': -1,
            'hidden': False,
            'alias': None,
    }

    # extract attributes from the first column
    vals = cols[0].split(':')
    while len(vals) > 1:
        val = vals.pop(0)
        if val.startswith('slot='):
            attrs['slot'] = int(val[5:])
        elif val == 'hidden':
            attrs['hidden'] = True
        elif val.startswith('alias='):
            attrs['alias'] = val[6:]
        elif not val:
            pass
        else:
            raise Exception('unknown attribute %s' % val)
    cols[0] = vals[0]

    return (attrs, cols)

def abi_parse(filename):
    """Parse a CSV file for ABI entries."""
    fp = open(filename) if filename != '-' else sys.stdin
    lines = [line.strip() for line in fp.readlines()
            if not line.startswith('#') and line.strip()]

    entry_dict = {}
    next_slot = 0
    for line in lines:
        attrs, cols = abi_parse_line(line)

        # post-process attributes
        if attrs['alias']:
            try:
                ent = entry_dict[attrs['alias']]
                slot = ent.slot
            except KeyError:
                raise Exception('failed to alias %s' % attrs['alias'])
        else:
            slot = next_slot
            next_slot += 1

        if attrs['slot'] < 0:
            attrs['slot'] = slot
        elif attrs['slot'] != slot:
            raise Exception('invalid slot in %s' % (line))

        ent = ABIEntry(cols, attrs)
        if entry_dict.has_key(ent.name):
            raise Exception('%s is duplicated' % (ent.name))
        entry_dict[ent.name] = ent

    entries = entry_dict.values()
    entries.sort()

    # sanity check
    i = 0
    for slot in xrange(next_slot):
        if entries[i].slot != slot:
            raise Exception('entries are not ordered by slots')
        if entries[i].alias:
            raise Exception('first entry of slot %d aliases %s'
                    % (slot, entries[i].alias))
        while i < len(entries) and entries[i].slot == slot:
            i += 1
    if i < len(entries):
        raise Exception('there are %d invalid entries' % (len(entries) - 1))

    return entries

class ABIPrinter(object):
    """MAPI Printer"""

    def __init__(self, entries):
        self.entries = entries

        # sort entries by their names
        self.entries_sorted_by_names = self.entries[:]
        self.entries_sorted_by_names.sort(lambda x, y: cmp(x.name, y.name))

        self.indent = ' ' * 3
        self.noop_warn = 'noop_warn'
        self.noop_generic = 'noop_generic'

        self.api_defines = []
        self.api_headers = ['"KHR/khrplatform.h"']
        self.api_call = 'KHRONOS_APICALL'
        self.api_entry = 'KHRONOS_APIENTRY'
        self.api_attrs = 'KHRONOS_APIATTRIBUTES'

    def c_header(self):
        return '/* This file is automatically generated by mapi_abi.py.  Do not modify. */'

    def c_includes(self):
        """Return includes of the client API headers."""
        defines = ['#define ' + d for d in self.api_defines]
        includes = ['#include ' + h for h in self.api_headers]
        return "\n".join(defines + includes)

    def c_mapi_table(self):
        """Return defines of the dispatch table size."""
        num_static_entries = 0
        for ent in self.entries:
            if not ent.alias:
                num_static_entries += 1

        return ('#define MAPI_TABLE_NUM_STATIC %d\n' + \
                '#define MAPI_TABLE_NUM_DYNAMIC %d') % (
                        num_static_entries, ABI_NUM_DYNAMIC_ENTRIES)

    def c_mapi_table_initializer(self, prefix):
        """Return the array initializer for mapi_table_fill."""
        entries = [ent.name for ent in self.entries if not ent.alias]
        pre = self.indent + '(mapi_proc) ' + prefix
        return pre + (',\n' + pre).join(entries)

    def c_mapi_table_spec(self):
        """Return the spec for mapi_init."""
        specv1 = []
        line = '"1'
        for ent in self.entries:
            if not ent.alias:
                line += '\\0"\n'
                specv1.append(line)
                line = '"'
            line += '%s\\0' % ent.name
        line += '";'
        specv1.append(line)

        return self.indent + self.indent.join(specv1)

    def _c_decl(self, ent, prefix, need_attr=True):
        """Return the C declaration for the entry."""
        decl = '%s %s %s%s(%s)' % (ent.c_return(), self.api_entry,
                prefix, ent.name, ent.c_params())
        if need_attr and self.api_attrs:
            decl += ' ' + self.api_attrs

        return decl

    def _c_cast(self, ent):
        """Return the C cast for the entry."""
        cast = '%s (%s *)(%s)' % (
                ent.c_return(), self.api_entry, ent.c_params())

        return cast

    def c_private_declarations(self, prefix):
        """Return the declarations of private functions."""
        decls = [self._c_decl(ent, prefix)
                for ent in self.entries if not ent.alias]

        return ";\n".join(decls) + ";"

    def c_public_dispatches(self, prefix):
        """Return the public dispatch functions."""
        dispatches = []
        for ent in self.entries:
            if ent.hidden:
                continue

            proto = self.api_call + ' ' + self._c_decl(ent, prefix)
            cast = self._c_cast(ent)

            ret = ''
            if ent.ret:
                ret = 'return '
            stmt1 = self.indent
            stmt1 += 'const struct mapi_table *tbl = u_current_get();'
            stmt2 = self.indent
            stmt2 += 'mapi_func func = ((const mapi_func *) tbl)[%d];' % (
                    ent.slot)
            stmt3 = self.indent
            stmt3 += '%s((%s) func)(%s);' % (ret, cast, ent.c_args())

            disp = '%s\n{\n%s\n%s\n%s\n}' % (proto, stmt1, stmt2, stmt3)
            dispatches.append(disp)

        return '\n\n'.join(dispatches)

    def c_stub_string_pool(self):
        """Return the string pool for use by stubs."""
        # sort entries by their names
        sorted_entries = self.entries[:]
        sorted_entries.sort(lambda x, y: cmp(x.name, y.name))

        pool = []
        offsets = {}
        count = 0
        for ent in sorted_entries:
            offsets[ent] = count
            pool.append('%s' % (ent.name))
            count += len(ent.name) + 1

        pool_str =  self.indent + '"' + \
                ('\\0"\n' + self.indent + '"').join(pool) + '";'
        return (pool_str, offsets)

    def c_stub_initializer(self, prefix, pool_offsets):
        """Return the initializer for struct mapi_stub array."""
        stubs = []
        for ent in self.entries_sorted_by_names:
            stubs.append('%s{ (mapi_func) %s%s, %d, (void *) %d }' % (
                self.indent, prefix, ent.name, ent.slot, pool_offsets[ent]))

        return ',\n'.join(stubs)

    def c_noop_functions(self, prefix, warn_prefix):
        """Return the noop functions."""
        noops = []
        for ent in self.entries:
            if ent.alias:
                continue

            proto = 'static ' + self._c_decl(ent, prefix)

            stmt1 = self.indent + '%s("%s%s");' % (
                    self.noop_warn, warn_prefix, ent.name)

            if ent.ret:
                stmt2 = self.indent + 'return (%s) 0;' % (ent.ret)
                noop = '%s\n{\n%s\n%s\n}' % (proto, stmt1, stmt2)
            else:
                noop = '%s\n{\n%s\n}' % (proto, stmt1)

            noops.append(noop)

        return '\n\n'.join(noops)

    def c_noop_initializer(self, prefix, use_generic):
        """Return an initializer for the noop dispatch table."""
        entries = [prefix + ent.name for ent in self.entries if not ent.alias]
        if use_generic:
            entries = [self.noop_generic] * len(entries)

        entries.extend([self.noop_generic] * ABI_NUM_DYNAMIC_ENTRIES)

        pre = self.indent + '(mapi_func) '
        return pre + (',\n' + pre).join(entries)

    def c_asm_gcc(self, prefix):
        asm = []
        to_name = None

        asm.append('__asm__(')
        for ent in self.entries:
            name = prefix + ent.name

            if ent.hidden:
                asm.append('".hidden %s\\n"' % (name))

            if ent.alias:
                asm.append('".globl %s\\n"' % (name))
                asm.append('".set %s, %s\\n"' % (name, to_name))
            else:
                asm.append('STUB_ASM_ENTRY("%s")"\\n"' % (name))
                asm.append('"\\t"STUB_ASM_CODE("%d")"\\n"' % (ent.slot))
                to_name = name
        asm.append(');')

        return "\n".join(asm)

    def output_for_lib(self):
        print self.c_header()
        print
        print '#ifdef MAPI_TMP_DEFINES'
        print self.c_includes()
        print '#undef MAPI_TMP_DEFINES'
        print '#endif /* MAPI_TMP_DEFINES */'
        print
        print '#ifdef MAPI_TMP_TABLE'
        print self.c_mapi_table()
        print '#undef MAPI_TMP_TABLE'
        print '#endif /* MAPI_TMP_TABLE */'
        print

        pool, pool_offsets = self.c_stub_string_pool()
        print '#ifdef MAPI_TMP_PUBLIC_STUBS'
        print 'static const char public_string_pool[] ='
        print pool
        print
        print 'static const struct mapi_stub public_stubs[] = {'
        print self.c_stub_initializer(self.prefix_lib, pool_offsets)
        print '};'
        print '#undef MAPI_TMP_PUBLIC_STUBS'
        print '#endif /* MAPI_TMP_PUBLIC_STUBS */'
        print

        print '#ifdef MAPI_TMP_PUBLIC_ENTRIES'
        print self.c_public_dispatches(self.prefix_lib)
        print '#undef MAPI_TMP_PUBLIC_ENTRIES'
        print '#endif /* MAPI_TMP_PUBLIC_ENTRIES */'
        print

        print '#ifdef MAPI_TMP_NOOP_ARRAY'
        print '#ifdef DEBUG'
        print
        print self.c_noop_functions(self.prefix_noop, self.prefix_lib)
        print
        print 'const mapi_func table_%s_array[] = {' % (self.prefix_noop)
        print self.c_noop_initializer(self.prefix_noop, False)
        print '};'
        print
        print '#else /* DEBUG */'
        print
        print 'const mapi_func table_%s_array[] = {' % (self.prefix_noop)
        print self.c_noop_initializer(self.prefix_noop, True)
        print '};'
        print '#endif /* DEBUG */'
        print '#undef MAPI_TMP_NOOP_ARRAY'
        print '#endif /* MAPI_TMP_NOOP_ARRAY */'
        print

        print '#ifdef MAPI_TMP_STUB_ASM_GCC'
        print self.c_asm_gcc(self.prefix_lib)
        print '#undef MAPI_TMP_STUB_ASM_GCC'
        print '#endif /* MAPI_TMP_STUB_ASM_GCC */'

    def output_for_app(self):
        print self.c_header()
        print
        print self.c_private_declarations(self.prefix_app)
        print
        print '#ifdef API_TMP_DEFINE_SPEC'
        print
        print 'static const char %s_spec[] =' % (self.prefix_app)
        print self.c_mapi_table_spec()
        print
        print 'static const mapi_proc %s_procs[] = {' % (self.prefix_app)
        print self.c_mapi_table_initializer(self.prefix_app)
        print '};'
        print
        print '#endif /* API_TMP_DEFINE_SPEC */'

class GLAPIPrinter(ABIPrinter):
    """OpenGL API Printer"""

    def __init__(self, entries):
        super(GLAPIPrinter, self).__init__(entries)

        self.api_defines = ['GL_GLEXT_PROTOTYPES']
        self.api_headers = ['"GL/gl.h"', '"GL/glext.h"']
        self.api_call = 'GLAPI'
        self.api_entry = 'APIENTRY'
        self.api_attrs = ''

        self.prefix_lib = 'gl'
        self.prefix_app = '_mesa_'
        self.prefix_noop = 'noop'

    def output_for_app(self):
        # not used
        pass

class ES1APIPrinter(GLAPIPrinter):
    """OpenGL ES 1.x API Printer"""

    def __init__(self, entries):
        super(ES1APIPrinter, self).__init__(entries)

        self.api_headers = ['"GLES/gl.h"', '"GLES/glext.h"']
        self.api_call = 'GL_API'
        self.api_entry = 'GL_APIENTRY'

class ES2APIPrinter(GLAPIPrinter):
    """OpenGL ES 2.x API Printer"""

    def __init__(self, entries):
        super(ES2APIPrinter, self).__init__(entries)

        self.api_headers = ['"GLES2/gl2.h"', '"GLES2/gl2ext.h"']
        self.api_call = 'GL_APICALL'
        self.api_entry = 'GL_APIENTRY'

class VGAPIPrinter(ABIPrinter):
    """OpenVG API Printer"""

    def __init__(self, entries):
        super(VGAPIPrinter, self).__init__(entries)

        self.api_defines = ['VG_VGEXT_PROTOTYPES']
        self.api_headers = ['"VG/openvg.h"', '"VG/vgext.h"']
        self.api_call = 'VG_API_CALL'
        self.api_entry = 'VG_API_ENTRY'
        self.api_attrs = 'VG_API_EXIT'

        self.prefix_lib = 'vg'
        self.prefix_app = 'vega'
        self.prefix_noop = 'noop'

def parse_args():
    printers = ['glapi', 'es1api', 'es2api', 'vgapi']
    modes = ['lib', 'app']

    parser = OptionParser(usage='usage: %prog [options] <filename>')
    parser.add_option('-p', '--printer', dest='printer',
            help='printer to use: %s' % (", ".join(printers)))
    parser.add_option('-m', '--mode', dest='mode',
            help='target user: %s' % (", ".join(modes)))

    options, args = parser.parse_args()
    if not args or options.printer not in printers or \
            options.mode not in modes:
        parser.print_help()
        sys.exit(1)

    return (args[0], options)

def main():
    printers = {
        'vgapi': VGAPIPrinter,
        'glapi': GLAPIPrinter,
        'es1api': ES1APIPrinter,
        'es2api': ES2APIPrinter
    }

    filename, options = parse_args()

    entries = abi_parse(filename)
    printer = printers[options.printer](entries)
    if options.mode == 'lib':
        printer.output_for_lib()
    else:
        printer.output_for_app()

if __name__ == '__main__':
    main()
