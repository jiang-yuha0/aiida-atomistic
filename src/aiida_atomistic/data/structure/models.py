import copy
import functools
import json
import typing as t
from pydantic import BaseModel, Field, field_validator, PrivateAttr, computed_field, model_validator
import numpy as np
import warnings

from aiida import orm
from aiida.common.constants import elements
from aiida.orm.nodes.data import Data

from aiida_atomistic.data.structure.site import SiteImmutable, SiteMutable

try:
    import ase  # noqa: F401
    from ase import io as ase_io

    has_ase = True
    ASE_ATOMS_TYPE = ase.Atoms
except ImportError:
    has_ase = False

    ASE_ATOMS_TYPE = t.Any

try:
    import pymatgen.core as core  # noqa: F401

    has_pymatgen = True
    PYMATGEN_MOLECULE = core.structure.Molecule
    PYMATGEN_STRUCTURE = core.structure.Structure
except ImportError:
    has_pymatgen = False

    PYMATGEN_MOLECULE = t.Any
    PYMATGEN_STRUCTURE = t.Any


from aiida_atomistic.data.structure.utils import (
    _get_valid_cell,
    _get_valid_pbc,
    atom_kinds_to_html,
    calc_cell_volume,
    create_automatic_kind_name,
    get_formula,
    ObservedArray,
    FrozenList,
    freeze_nested,
    get_dimensionality,
    _check_valid_sites
)

_MASS_THRESHOLD = 1.0e-3
# Threshold to check if the sum is one or not
_SUM_THRESHOLD = 1.0e-6
# Default cell
_DEFAULT_CELL = [[0.0, 0.0, 0.0]] * 3

_valid_symbols = tuple(i["symbol"] for i in elements.values())
_atomic_masses = {el["symbol"]: el["mass"] for el in elements.values()}
_atomic_numbers = {data["symbol"]: num for num, data in elements.items()}

class StructureBaseModel(BaseModel):
    
    #sites: t.Optional[t.List[SiteMutable]]
    pbc: t.Optional[t.List[bool]] = Field(min_length=3, max_length=3, default_factory=lambda: [True, True, True])
    cell: t.Optional[t.List[t.List[float]]] = Field(default_factory=lambda: _DEFAULT_CELL)
    
    class Config:
        from_attributes = True
        frozen = False
        arbitrary_types_allowed=True
    
    @field_validator('pbc')
    @classmethod
    def validate_pbc(cls, v: t.List[bool]) -> t.Any:
        
        if not isinstance(v, list):
            if cls._mutable.default:
                warnings.warn("pbc should be a list")
            else:
                raise ValueError("pbc must be a list")
            return v
        
        if len(v) != 3:
            if cls._mutable.default:
                warnings.warn("pbc should be a list of length 3")
            else:
                raise ValueError("pbc must be a list of length 3")
            return v
        
        if not cls._mutable.default:
            return freeze_nested(v)
        
        return v
    
    @field_validator('cell')
    @classmethod
    def validate_cell(cls, v: t.List[t.List[float]]) -> t.Any:
        
        if not isinstance(v, list):
            if cls._mutable.default:
                warnings.warn("cell should be a 3x3 list")
            else:
                raise ValueError("cell must be a 3x3 list")
            return v
        
        if not cls._mutable.default:
            return freeze_nested(v)
        
        return v
    
    @computed_field
    @property
    def cell_volume(self) -> float:
        return calc_cell_volume(self.cell)
    
    @computed_field
    @property
    def dimensionality(self) -> dict:
        return get_dimensionality(self.pbc, self.cell)
    
    @computed_field
    @property
    def charges(self) -> FrozenList[float]:
        return FrozenList([site.charge for site in self.sites])
    
    @computed_field
    @property
    def magmoms(self) -> FrozenList[FrozenList[float]]:
        return FrozenList([site.magmom for site in self.sites])
    
    @computed_field
    @property
    def masses(self) -> FrozenList[float]:
        return FrozenList([site.mass for site in self.sites])
    
    @computed_field
    @property
    def kinds(self) -> FrozenList[str]:
        return FrozenList([site.kind_name for site in self.sites])
    
    @computed_field
    @property
    def symbols(self) -> FrozenList[str]:
        return FrozenList([site.symbol for site in self.sites])
    
    @computed_field
    @property
    def positions(self) -> FrozenList[FrozenList[float]]:
        return FrozenList([site.position for site in self.sites])
    
    @computed_field
    @property
    def formula(self) -> str:
        return get_formula(self.symbols)
    
class MutableStructureModel(StructureBaseModel):
    
    _mutable = True
    
    sites : t.Optional[t.List[SiteMutable]]= Field(default_factory=list)
    
class ImmutableStructureModel(StructureBaseModel):
    
    _mutable = False
    
    sites : t.List[SiteImmutable]
    
    class Config:
        from_attributes = True
        frozen = True
        arbitrary_types_allowed=True
        
    @model_validator(mode='before')
    def check_minimal_requirements(cls, data):
        if not data.get("sites", None):
            raise ValueError("The structure must contain at least one site")
        else:
            _check_valid_sites(data["sites"])
        if not data.get("cell", None):
            raise ValueError("The structure must contain a cell")
        if not data.get("pbc", None):    
            raise ValueError("The structure must contain periodic boundary conditions")
        
        # check sites not one over the other. see the append_atom method.
        return data
