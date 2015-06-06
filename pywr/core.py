#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import networkx as nx
import numpy as np
import inspect
import pandas
import datetime
import xml.etree.ElementTree as ET
from six import with_metaclass

import warnings
warnings.simplefilter(action = "ignore", category = FutureWarning)
warnings.simplefilter(action = "ignore", category = UnicodeWarning)

from .licenses import LicenseCollection

inf = float('inf')

class Model(object):
    def __init__(self, solver=None, parameters=None):
        self.graph = nx.DiGraph()
        self.metadata = {}
        self.parameters = {
            # default parameter values
            'timestamp_start': pandas.to_datetime('2015-01-01'),
            'timestamp_finish': pandas.to_datetime('2015-12-31'),
            'timestep': datetime.timedelta(1),
        }
        if parameters is not None:
            self.parameters.update(parameters)
        self.data = {}
        self.dirty = True
        
        if solver is not None:
            # use specific solver
            try:
                self.solver = SolverMeta.solvers[solver.lower()]
            except KeyError:
                raise KeyError('Unrecognised solver: {}'.format(solver))
        else:
            # use default solver
            self.solver = solvers.SolverGLPK()
        
        self.node = {}
        self.group = {}
        
        self.reset()
    
    def check(self):
        nodes = self.graph.nodes()
        for node in nodes:
            node.check()

    def nodes(self):
        return self.graph.nodes()
    
    def find_all_routes(self, type1, type2, valid=None):
        '''Find all routes between two nodes or types of node'''
        
        nodes = self.graph.nodes()
        
        if inspect.isclass(type1):
            # find all nodes of type1
            type1_nodes = []
            for node in nodes:
                if isinstance(node, type1):
                    type1_nodes.append(node)
        else:
            type1_nodes = [type1]
        
        if inspect.isclass(type2):
            # find all nodes of type2
            type2_nodes = []
            for node in nodes:
                if isinstance(node, type2):
                    type2_nodes.append(node)
        else:
            type2_nodes = [type2]
        
        # find all routes between type1_nodes and type2_nodes
        all_routes = []
        for node1 in type1_nodes:
            for node2 in type2_nodes:
                for route in nx.all_simple_paths(self.graph, node1, node2):
                    is_valid = True
                    if valid is not None and len(route) > 2:
                        for node in route[1:-1]:
                            if not isinstance(node, valid):
                                is_valid = False
                    if is_valid:
                        all_routes.append(route)
        
        return all_routes
    
    def step(self):
        '''Step the model forward by one day'''
        ret = self.solve()
        self.timestamp += self.parameters['timestep']
        return ret

    def solve(self):
        '''Call solver to solve the current timestep'''
        return self.solver.solve(self)
    
    def run(self, until_date=None, until_failure=False):
        '''Run model until exit condition is reached
        
        Parameters
        ----------
        until_date : datetime
            Stop model when date is reached
        until_failure: bool
            Stop model run when failure condition occurs
        
        Returns the number of timesteps that were run.
        '''
        if self.timestamp > self.parameters['timestamp_finish']:
            return
        timesteps = 0
        while True:
            ret = self.step()
            timesteps += 1
            # TODO: more complex assessment of "failure"
            if until_failure is True and ret[1] != ret[2]:
                return timesteps
            elif until_date and self.timestamp > until_date:
                return timesteps
            elif self.timestamp > self.parameters['timestamp_finish']:
                return timesteps
    
    def reset(self):
        '''Reset model to it's initial conditions'''
        # TODO: this will need more, e.g. reservoir states, license states
        self.timestamp = self.parameters['timestamp_start']
    
    @property
    def xml(self):
        """Serialize the Model to XML"""
        raise NotImplementedError('TODO')
    
    @classmethod
    def from_xml(cls, xml):
        """Deserialize a Model from XML"""
        xml_solver = xml.find('solver')
        if xml_solver is not None:
            solver = xml_solver.get('name')
        else:
            solver = None
        
        model = Model(solver=solver)
        
        # parse metadata
        xml_metadata = xml.find('metadata')
        if xml_metadata is not None:
            for xml_metadata_item in xml_metadata.getchildren():
                key = xml_metadata_item.tag.lower()
                value = xml_metadata_item.text.strip()
                model.metadata[key] = value
        
        # parse model parameters
        for xml_parameters in xml.findall('parameters'):
            for xml_parameter in xml_parameters.getchildren():
                key, parameter = xmlutils.parse_parameter(model, xml_parameter)
                model.parameters[key] = parameter

        # parse data
        xml_datas = xml.find('data')
        if xml_datas:
            for xml_data in xml_datas.getchildren():
                tag = xml_data.tag.lower()
                name = xml_data.get('name')
                properties = {}
                for child in xml_data.getchildren():
                    properties[child.tag] = child.text
                if properties['type'] == 'pandas':
                    # TODO: better handling of british/american dates (currently assumes british)
                    df = pandas.read_csv(properties['path'], index_col=0, parse_dates=True, dayfirst=True)
                    df = df[properties['column']]
                    ts = Timeseries(df)
                    model.data[name] = ts
                else:
                    raise NotImplementedError()
        
        # parse nodes
        for node_xml in xml.find('nodes'):
            tag = node_xml.tag.lower()
            node_cls = node_registry[tag]
            node = node_cls.from_xml(model, node_xml)

        # parse edges
        xml_edges = xml.find('edges')
        for xml_edge in xml_edges.getchildren():
            tag = xml_edge.tag.lower()
            if tag != 'edge':
                raise ValueError()
            from_name = xml_edge.get('from')
            to_name = xml_edge.get('to')
            from_node = model.node[from_name]
            to_node = model.node[to_name]
            slot = xml_edge.get('slot')
            if slot is not None:
                slot = int(slot)
            to_slot = xml_edge.get('to_slot')
            if to_slot is not None:
                to_slot = int(to_slot)
            from_node.connect(to_node, slot=slot, to_slot=to_slot)

        # parse groups
        xml_groups = xml.find('groups')
        if xml_groups:
            for xml_group in xml_groups.getchildren():
                tag = xml_group.tag.lower()
                if tag != 'group':
                    raise ValueError()
                name = xml_group.get('name')
                group = Group(model, name)
                for xml_member in xml_group.find('members'):
                    name = xml_member.get('name')
                    node = model.node[name]
                    group.nodes.add(node)
                licensecollection_xml = xml_group.find('licensecollection')
                if licensecollection_xml:
                    group.licenses = LicenseCollection.from_xml(licensecollection_xml)
        
        return model

class SolverMeta(type):
    solvers = {}
    def __new__(cls, clsname, bases, attrs):
        newclass = super(SolverMeta, cls).__new__(cls, clsname, bases, attrs)
        cls.solvers[newclass.name.lower()] = newclass
        return newclass

class Solver(with_metaclass(SolverMeta)):
    '''Solver base class from which all solvers should inherit'''
    name = 'default'
    def solve(self, model):
        raise NotImplementedError('Solver should be subclassed to provide solve()')

class Parameter(object):
    def __init__(self, value=None):
        self._value = value
    
    def value(self, index=None):
        return self._value

class ParameterFunction(object):
    def __init__(self, parent, func):
        self._parent = parent
        self._func = func

    def value(self, index=None):
        return self._func(self._parent, index)

class Timeseries(object):
    def __init__(self, df):
        self.df = df
    
    def value(self, index):
        return self.df[index]

class Variable(object):
    def __init__(self, initial=0.0):
        self._initial = initial
        self._value = initial

    def value(self, index=None):
        return self._value

# node subclasses are stored in a dict for convenience
node_registry = {}
class NodeMeta(type):
    def __new__(meta, name, bases, dct):
        return super(NodeMeta, meta).__new__(meta, name, bases, dct)
    def __init__(cls, name, bases, dct):
        super(NodeMeta, cls).__init__(name, bases, dct)
        node_registry[name.lower()] = cls

class Node(with_metaclass(NodeMeta)):
    '''Base object from which all other nodes inherit'''
    
    def __init__(self, model, position=None, name=None, **kwargs):
        self.model = model
        model.graph.add_node(self)
        self.color = 'black'
        self.position = position
        self.__name = None
        self.name = name
        
        self.properties = {
            'cost': Parameter(value=0.0)
        }
    
    def __repr__(self):
        if self.name:
            return '<{} "{}">'.format(self.__class__.__name__, self.name)
        else:
            return '<{} "{}">'.format(self.__class__.__name__, hex(id(self)))
    
    @property
    def name(self):
        return self.__name
    
    @name.setter
    def name(self, name):
        try:
            del(self.model.node[self.__name])
        except KeyError:
            pass
        self.__name = name
        self.model.node[name] = self
    
    def connect(self, node, slot=None, to_slot=None):
        '''Create a connection from this Node to another Node'''
        if self.model is not node.model:
            raise RuntimeError("Can't connect Nodes in different Models")
        self.model.graph.add_edge(self, node)
        if slot is not None:
            self.slots[slot] = node
        if to_slot is not None:
            node.slots[to_slot] = self
    
    def disconnect(self, node=None):
        '''Remove a connection from this Node to another Node
        
        If another Node is not specified, all connections from this Node will
        be removed.
        '''
        if node is not None:
            self.model.graph.remove_edge(self, node)
        else:
            neighbors = self.model.graph.neighbors(self)
            for neighbor in neighbors:
                self.model.graph.remove_edge(self, neighbor)
    
    def check(self):
        if not isinstance(self.position, (tuple, list,)):
            raise TypeError('{} position has invalid type ({})'.format(self, type(self.position)))
        if not len(self.position) == 2:
            raise ValueError('{} position has invalid length ({})'.format(self, len(self.position)))

    def commit(self, volume, chain):
        '''Commit a volume of water actually supplied
        
        This should be implemented by the various node classes
        '''
        pass
    
    @property
    def xml(self):
        xml = ET.fromstring('<{} />'.format(self.__class__.__name__.lower()))
        xml.set('name', self.name)
        xml.set('x', str(self.position[0]))
        xml.set('y', str(self.position[1]))
        # TODO: delegate this
        for key, prop in self.properties.items():
            prop_xml = ET.fromstring('<parameter />')
            prop_xml.set('key', key)
            prop_xml.set('type', 'const')
            prop_xml.text = str(prop._value)
            xml.append(prop_xml)
        return xml
    
    @classmethod
    def from_xml(cls, model, xml):
        tag = xml.tag.lower()
        node_cls = node_registry[tag]
        name = xml.get('name')
        x = float(xml.get('x'))
        y = float(xml.get('y'))
        node = node_cls(model, name=name, position=(x, y,))
        for prop_xml in xml.findall('parameter'):
            key, prop = xmlutils.parse_parameter(model, prop_xml)
            node.properties[key] = prop
        for var_xml in xml.findall('variable'):
            key, prop = xmlutils.parse_variable(model, var_xml)
            node.properties[key] = prop
        return node

class Supply(Node):
    def __init__(self, *args, **kwargs):
        Node.__init__(self, *args, **kwargs)
        self.color = '#F26C4F' # light red
        
        max_flow = kwargs.pop('max_flow', 0.0)
        if callable(max_flow):
            self.properties['max_flow'] = ParameterFunction(self, max_flow)
        else:
            self.properties['max_flow'] = Parameter(value=max_flow)
        
        self.licenses = None
    
    def commit(self, volume, chain):
        super(Supply, self).commit(volume, chain)
        if self.licenses is not None:
            self.licenses.commit(volume)

    @classmethod
    def from_xml(cls, model, xml):
        node = Node.from_xml(model, xml)
        licensecollection_xml = xml.find('licensecollection')
        if licensecollection_xml is not None:
            node.licenses = LicenseCollection.from_xml(licensecollection_xml)
        return node

class Demand(Node):
    def __init__(self, *args, **kwargs):
        Node.__init__(self, *args, **kwargs)
        self.color = '#FFF467' # light yellow
        
        self.properties['demand'] = Parameter(value=kwargs.pop('demand',10.0))

class Link(Node):
    def __init__(self, *args, **kwargs):
        Node.__init__(self, *args, **kwargs)
        self.color = '#A0A0A0' # 45% grey
        
        if 'max_flow' in kwargs:
            max_flow = kwargs.pop('max_flow', 0.0)
            if callable(max_flow):
                self.properties['max_flow'] = ParameterFunction(self, max_flow)
            else:
                self.properties['max_flow'] = Parameter(value=max_flow)

class Blender(Link):
    def __init__(self, *args, **kwargs):
        Link.__init__(self, *args, **kwargs)
        self.slots = {1: None, 2: None}

        if 'ratio' in kwargs:
            self.properties['ratio'] = Parameter(value=kwargs['ratio'])
        else:
            self.properties['ratio'] = Parameter(value=0.5)

class Catchment(Node):
    def __init__(self, *args, **kwargs):
        Node.__init__(self, *args, **kwargs)
        self.color = '#82CA9D' # green
        
        flow = kwargs.pop('flow', 2.0)
        if callable(flow):
            self.properties['flow'] = ParameterFunction(self, flow)
        else:
            self.properties['flow'] = Parameter(value=flow)        
    
    def check(self):
        Node.check(self)
        successors = self.model.graph.successors(self)
        if not len(successors) == 1:
            raise ValueError('{} has invalid number of successors ({})'.format(self, len(successors)))

class River(Node):
    def __init__(self, *args, **kwargs):
        Node.__init__(self, *args, **kwargs)
        self.color = '#6ECFF6' # blue

class RiverSplit(River):
    def __init__(self, *args, **kwargs):
        River.__init__(self, *args, **kwargs)
        self.slots = {1: None, 2: None}
        
        if 'split' in kwargs:
            self.properties['split'] = Parameter(value=kwargs['split'])
        else:
            self.properties['split'] = Parameter(value=0.5)

class Terminator(Node):
    pass

class RiverGauge(River):
    pass

class RiverAbstraction(Supply, River):
    pass

class Reservoir(Supply, Demand):
    def __init__(self, *args, **kwargs):
        super(Reservoir, self).__init__(*args, **kwargs)
        
        # reservoir cannot supply more than it's current volume
        def func(parent, index):
            return self.properties['current_volume'].value(index)
        self.properties['max_flow'] = ParameterFunction(self, func)
        
        def func(parent, index):
            current_volume = self.properties['current_volume'].value(index)
            max_volume = self.properties['max_volume'].value(index)
            return max_volume - current_volume
        self.properties['demand'] = ParameterFunction(self, func)

    def commit(self, volume, chain):
        super(Reservoir, self).commit(volume, chain)
        # update the volume remaining in the reservoir
        if chain == 'first':
            # reservoir supplied some water
            self.properties['current_volume']._value -= volume
        elif chain == 'last':
            # reservoir received some water
            self.properties['current_volume']._value += volume

    def check(self):
        super(Reservoir, self).check()
        index = self.model.timestamp
        # check volume doesn't exceed maximum volume
        assert(self.properties['max_volume'].value(index) >= self.properties['current_volume'].value(index))

class Group(object):
    def __init__(self, model, name, nodes=None):
        self.model = model
        if nodes is None:
            self.nodes = set()
        else:
            self.nodes = set(nodes)
        self.__name = name
        self.name = name
        self.licenses = None

    @property
    def name(self):
        return self.__name
    
    @name.setter
    def name(self, name):
        try:
            del(self.model.group[self.__name])
        except KeyError:
            pass
        self.__name = name
        self.model.group[name] = self

from . import solvers
from . import xmlutils
