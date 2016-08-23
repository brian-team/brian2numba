'''
Module providing `numbaCodeObject`.
'''
import numpy as np
import math
from tempfile import tempdir

try:
    import hashlib
except ImportError:
    import md5 as hashlib

from brian2.core.preferences import prefs, BrianPreference
from brian2.core.variables import (DynamicArrayVariable, ArrayVariable,
                                   AuxiliaryVariable, Subexpression)

from brian2.codegen.codeobject import CodeObject

from brian2.codegen.templates import Templater
from brian2.codegen.targets import codegen_targets
from brian2.utils.stringtools import get_identifiers

from numba_generator import NumbaCodeGenerator, get_numpy_dtype


__all__ = ['NumbaCodeObject']

# Preferences
prefs.register_preferences(
    'codegen.runtime.numba',
    'Numba runtime codegen preferences',
    discard_units = BrianPreference(
        default=False,
        docs='''
        Whether to change the namespace of user-specifed functions to remove
        units.
        '''
        )
    )


class NumbaCodeObject(CodeObject):
    '''
    Execute code using Numba
    
    '''
    templater = Templater('brian2numba', '.py_',
        env_globals={'numpy_dtype': get_numpy_dtype,
                     'dtype': np.dtype})
    generator_class = NumbaCodeGenerator
    class_name = 'numba'

    def __init__(self, owner, code, variables, variable_indices,
                 template_name, template_source, name='numba_code_object*'):
        from brian2.devices.device import get_device
        self.device = get_device()
        self.namespace = {'_owner': owner,
                          # TODO: This should maybe go somewhere else
                          'logical_not': np.logical_not, 'log10':math.log10}
        CodeObject.__init__(self, owner, code, variables, variable_indices,
                            template_name, template_source, name=name)
        self.variables_to_namespace()

    @staticmethod
    def is_available():
        try:
            import numba
        except ImportError:
            return False
        return True

    def variables_to_namespace(self):

        # Variables can refer to values that are either constant (e.g. dt)
        # or change every timestep (e.g. t). We add the values of the
        # constant variables here and add the names of non-constant variables
        # to a list

        # A list containing tuples of name and a function giving the value
        self.nonconstant_values = []

        for name, var in self.variables.iteritems():
            if isinstance(var, (AuxiliaryVariable, Subexpression)):
                continue
            try:
                value = var.get_value()
            except (TypeError, AttributeError):
                # A dummy Variable without value or a function
                self.namespace[name] = var
                continue

            if isinstance(var, ArrayVariable):
                self.namespace[self.device.get_array_name(var,
                                                            self.variables)] = value
                self.namespace['_num'+name] = var.get_len()
                if var.scalar and var.constant:
                    self.namespace[name] = value.item()
            else:
                self.namespace[name] = value

            if isinstance(var, DynamicArrayVariable):
                dyn_array_name = self.generator_class.get_array_name(var,
                                                                    access_data=False)
                self.namespace[dyn_array_name] = self.device.get_value(var,
                                                                       access_data=False)
            # Also provide the Variable object itself in the namespace (can be
            # necessary for resize operations, for example)
            self.namespace['_var_'+name] = var

        # Get all identifiers in the code -- note that this is not a smart
        # function, it will get identifiers from strings, comments, etc. This
        # is not a problem here, since we only use this list to filter out
        # things. If we include something incorrectly, this only means that we
        # will pass something into the namespace unnecessarily.
        all_identifiers = get_identifiers(self.code)
        # Filter out all unneeded objects
        self.namespace = {k: v for k, v in self.namespace.iteritems()
                          if k in all_identifiers}

        # There is one type of objects that we have to inject into the
        # namespace with their current value at each time step: dynamic
        # arrays that change in size during runs, where the size change is not
        # initiated by the template itself
        for name, var in self.variables.iteritems():
            if (isinstance(var, DynamicArrayVariable) and
                    var.needs_reference_update):
                array_name = self.device.get_array_name(var, self.variables)
                if array_name in self.namespace:
                    self.nonconstant_values.append((array_name, var.get_value))
                if '_num'+name in self.namespace:
                    self.nonconstant_values.append(('_num'+name, var.get_len))

    def update_namespace(self):
        # update the values of the non-constant values in the namespace
        for name, func in self.nonconstant_values:
            self.namespace[name] = func()

    def compile(self):
        super(NumbaCodeObject, self).compile()
        outfile = tempdir + '/' + hashlib.md5(str(self.code).encode('utf-8')).hexdigest() + '.py'
        with open(outfile, "w") as f:
            f.write(self.code)
        execfile(outfile, self.namespace)
        print self.code

    def run(self):
        code = 'main(namespace)'
        self.namespace['namespace'] = self.namespace
        exec code in self.namespace
        if '_return_values' in self.namespace:
            return self.namespace['_return_values']

codegen_targets.add(NumbaCodeObject)
