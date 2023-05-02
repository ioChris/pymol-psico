'''
(c) Thomas Holder, Schrodinger Inc.
'''

from pymol import cmd, CmdException


def get_fixed_indices(selection, state, _self):
    fixed_list = []
    _self.iterate_state(state, selection,
            '_append(flags & 0x8)',
            space={'_append': fixed_list.append})
    return [idx for (idx, fixed) in enumerate(fixed_list) if fixed]


def load_or_update(molstr, name, sele, state, _self):
    with _self.lockcm:
        _load_or_update(molstr, name, sele, state, _self)


def _load_or_update(molstr, name, sele, state, _self):
    update = not name

    if update:
        name = _self.get_unused_name('_minimized')
    else:
        _self.delete(name)

    _self.load_raw(molstr, 'mol', name, 1, zoom=0)

    try:
        from psico.fitting import xfit
        xfit(name, sele, 1, state, match='none', cycles=100, guide=0)
    except ImportError:
        _self.fit(name, sele, 1, state, cycles=5, matchmaker=-1)
    except Exception as e:
        print('xfit failed: {}'.format(e))

    if update:
        _self.update(sele, name, state, 1, matchmaker=0)
        _self.delete(name)


def randomize_coords_if_collapsed(selection, state, fancy=True, _self=cmd):
    '''If all coordinates are the same (collapsed into one point), then
    randomize them.

    :param fancy: Arrange atoms in a circle (this works better for openbabel)
    :type fancy: bool
    '''
    coords = _self.get_coords(selection, state)

    if len(coords) < 2 or coords.std(0).sum() > 1e-3:
        return

    import numpy.random

    if fancy:
        # puts x,y on a circle
        angles = numpy.linspace(0, 2 * numpy.pi, len(coords), False)
        width = len(coords)**(1 / 3.)
        coords[:, 0] += numpy.sin(angles) * width
        coords[:, 1] += numpy.cos(angles) * width

    coords += numpy.random.random_sample(coords.shape) - 0.5

    _self.load_coords(coords, selection, state)


def minimize_ob(selection='enabled', state=-1, ff='UFF', nsteps=500,
        conv=0.0001, cutoff=0, cut_vdw=6.0, cut_elec=8.0,
        name='', quiet=1, _self=cmd):
    '''
DESCRIPTION

    Emergy minimization with openbabel

    Supports fixed atoms (flag fix)

ARGUMENTS

    selection = str: atom selection

    state = int: object state {default: -1}

    ff = GAFF|MMFF94s|MMFF94|UFF|Ghemical: force field {default: UFF}

    nsteps = int: number of steps {default: 500}
    '''
    import openbabel as ob

    try:
        # OB 3.x
        from openbabel import openbabel as ob  # noqa: F811 Redefinition of unused
    except ImportError:
        # OB 2.x
        pass

    state = int(state)

    sele = _self.get_unused_name('_sele')
    natoms = _self.select(sele, selection, 0)

    try:
        if natoms == 0:
            raise CmdException('empty selection')

        randomize_coords_if_collapsed(sele, state, _self=_self)

        ioformat = 'mol'
        molstr = _self.get_str(ioformat, sele, state)

        obconversion = ob.OBConversion()
        obconversion.SetInAndOutFormats(ioformat, ioformat)

        mol = ob.OBMol()
        obconversion.ReadString(mol, molstr)

        # add hydrogens
        orig_ids = [a.GetId() for a in ob.OBMolAtomIter(mol)]
        mol.AddHydrogens()
        added_ids = set(a.GetId() for a in ob.OBMolAtomIter(mol)).difference(orig_ids)

        consttrains = ob.OBFFConstraints()
        consttrains.Setup(mol)

        # atoms with "flag fix"
        fixed_indices = get_fixed_indices(sele, state, _self)
        for idx in fixed_indices:
            consttrains.AddAtomConstraint(idx + 1)

        # setup forcefield (one of: GAFF, MMFF94s, MMFF94, UFF, Ghemical)
        ff = ob.OBForceField.FindForceField(ff)
        if ff is None:
            raise CmdException("FindForceField returned None, please check "
                    "BABEL_LIBDIR and BABEL_DATADIR")
        ff.Setup(mol, consttrains)

        if int(cutoff):
            ff.EnableCutOff(True)
            ff.SetVDWCutOff(float(cut_vdw))
            ff.SetElectrostaticCutOff(float(cut_elec))

        # run minimization
        ff.SteepestDescent(int(nsteps) // 2, float(conv))
        ff.ConjugateGradients(int(nsteps) // 2, float(conv))
        ff.GetCoordinates(mol)

        # remove previously added hydrogens
        for hydro_id in added_ids:
            mol.DeleteAtom(mol.GetAtomById(hydro_id))

        molstr = obconversion.WriteString(mol)
        load_or_update(molstr, name, sele, state, _self)

        if not int(quiet):
            print(' Energy: %8.2f %s' % (ff.Energy(), ff.GetUnit()))
    finally:
        _self.delete(sele)


def minimize_rdkit(selection='enabled', state=-1, ff='MMFF94', nsteps=200,
        name='', quiet=1, _self=cmd):
    '''
DESCRIPTION

    Emergy minimization with RDKit

    Supports fixed atoms (flag fix)

ARGUMENTS

    selection = str: atom selection

    state = int: object state {default: -1}

    ff = MMFF94s|MMFF94|UFF: force field {default: MMFF94}

    nsteps = int: number of steps {default: 200}
    '''
    from rdkit import Chem
    from rdkit.Chem import AllChem

    state = int(state)

    sele = _self.get_unused_name('_sele')
    natoms = _self.select(sele, selection, 0)

    try:
        if natoms == 0:
            raise CmdException('empty selection')

        randomize_coords_if_collapsed(sele, state, _self=_self)

        molstr = _self.get_str('mol', sele, state)
        mol = Chem.MolFromMolBlock(molstr, True, False)

        if mol is None:
            raise CmdException('Failed to load molecule into RDKit. '
                    'Please check bond orders and formal charges.')

        # setup forcefield
        if ff.startswith('MMFF'):
            ff = AllChem.MMFFGetMoleculeForceField(mol,
                    AllChem.MMFFGetMoleculeProperties(mol, ff,
                        0 if int(quiet) else 1))
        elif ff == 'UFF':
            ff = AllChem.UFFGetMoleculeForceField(mol)
        else:
            raise CmdException('unknown forcefield: ' + ff)

        if ff is None:
            raise CmdException('forcefield setup failed')

        # atoms with "flag fix"
        for idx in get_fixed_indices(sele, state, _self):
            ff.AddFixedPoint(idx)

        # run minimization
        if ff.Minimize(int(nsteps)) != 0:
            print(" Warning: minimization did not converge")

        molstr = Chem.MolToMolBlock(mol)
        load_or_update(molstr, name, sele, state, _self)

        if not int(quiet):
            print(' Energy: %8.2f %s' % (ff.CalcEnergy(), 'kcal/mol'))
    finally:
        _self.delete(sele)


def clean_ob(selection, present='', state=-1, fix='', restrain='',
        method='mmff', save_undo=1, message=None,
        _self=cmd):
    '''
DESCRIPTION

    Replacement for pymol.computing._clean, using openbabel.

    Side effects: clears "fix" flag if "present" argument is given.

    import pymol.computing
    import psico.minimizing
    pymol.computing._clean = psico.minimizing.clean_ob

SEE ALSO

    minimize_ob
    '''
    if present:
        _self.flag('fix', present, 'set')
        _self.flag('fix', selection, 'clear')
        selection = '({})|({})'.format(selection, present)

    ff = {'mmff': 'MMFF94'}.get(method, method)
    minimize_ob(selection, state, ff, nsteps=50, _self=_self)

    if present:
        _self.flag('fix', present, 'clear')


cmd.extend('clean_ob', clean_ob)
cmd.extend('minimize_ob', minimize_ob)
cmd.extend('minimize_rdkit', minimize_rdkit)

cmd.auto_arg[0].update([
    ('clean_ob', cmd.auto_arg[0]['zoom']),
    ('minimize_ob', cmd.auto_arg[0]['zoom']),
    ('minimize_rdkit', cmd.auto_arg[0]['zoom']),
])

# vi:expandtab:smarttab
