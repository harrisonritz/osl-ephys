"""Minimum norm source localization using MNE-Python
"""
    
# Authors: Chetan Gohil <chetan.gohil@psych.ox.ac.uk>
#          Mats van Es <mats.vanes@psych.ox.ac.uk>


import os
import os.path as op
import subprocess
import pickle
from pathlib import Path

import mne
from mne.coreg import Coregistration
from mne.io import read_info
import numpy as np
import matplotlib.pyplot as plt

from . import rhino, beamforming, parcellation, sign_flipping, freesurfer_utils
from ..report import src_report
from ..utils.logger import log_or_print

import logging

logger = logging.getLogger(__name__)


def minimum_norm(
    outdir,
    subject,
    data,
    chantypes,
    method,
    rank,
    depth,
    loose,
    ):
    """Run minimum norm source localization.
    
    Parameters
    ----------
    outdir : str
        Output directory.
    subject : str
        Subject ID.
    data : mnep.io.Raw, mne.Epochs  
        Preprocessed data.
    chantypes : list
        List of channel types to include.
    method : str
        Inverse method.
    rank : int
        Rank of the data covariance matrix.
    morph : bool, str
        Morph method, e.g. fsaverage. Can be False.
    depth : float
        Depth weighting.
    loose : float
        Loose parameter.
    reg : float
        Regularization parameter.
    reportdir : str
        Report directory.
    """

    log_or_print("*** RUNNING MNE SOURCE LOCALIZATION ***")
    
    fwd_fname = freesurfer_utils.get_freesurfer_files(outdir, subject)['fwd_model']
    coreg_files = freesurfer_utils.get_coreg_filenames(outdir, subject)
    
    noise_cov = calc_noise_cov(data, rank, chantypes)
    
    fwd = mne.read_forward_solution(fwd_fname)
    log_or_print(f"*** Making {method} inverse solution ***")
    inverse_operator = mne.minimum_norm.make_inverse_operator(data.info, fwd, noise_cov, loose=loose, depth=depth)
      
    log_or_print(f"*** Saving {method} inverse operator ***")
    mne.minimum_norm.write_inverse_operator(coreg_files['inverse_operator'].format(method), inverse_operator, overwrite=True)
    return inverse_operator
    

def apply_inverse_solution(
    outdir,
    subject,
    data,
    method,
    lambda2,
    pick_ori,
    inverse_operator=None,
    morph="fsaverage",
    save=False,
    ):
    """ Apply previously computed minimum norm inverse solution.
    
    Parameters
    ----------
    outdir : str
        Output directory.
    subject : str
        Subject ID.
    data : mne.io.Raw, mne.Epochs
        Raw or Epochs object.
    inverse_operator : mne.minimum_norm.InverseOperator
        Inverse operator.
    method : str
        Inverse method.
    lambda2 : float
        Regularization parameter.
    pick_ori : str
        Orientation to pick.
    morph : bool, str
        Morph method, e.g. fsaverage. Can be False.
    save : bool
        Save source estimate (default: False).
    """
    
    coreg_files = freesurfer_utils.get_coreg_filenames(outdir, subject)
    if inverse_operator is None:
        inverse_operator = mne.minimum_norm.read_inverse_operator(coreg_files['inverse_operator'].format(method))
    
    log_or_print(f"*** Applying {method} inverse solution ***")

    if isinstance(data, mne.Epochs):
        stc = mne.minimum_norm.apply_inverse_epochs(data, inverse_operator, lambda2=lambda2, method=method, pick_ori=pick_ori)    
    else:
        stc = mne.minimum_norm.apply_inverse_raw(data, inverse_operator, lambda2=lambda2, method=method, pick_ori=pick_ori)
    
    if morph:
        log_or_print(f"*** Morphing source estimate to {morph} ***")
        src_from = mne.read_source_spaces(coreg_files['source_space'])
        morph = morph_surface(outdir, subject, src_from, subject_to=morph)
        morph.save(op.join(outdir, subject, "mne_src", morph), overwrite=True)
        stc = morph.apply(stc)
    
    if save:     
        log_or_print(f"*** Saving Source estimate ***")   
        if isinstance(data, mne.Epochs):
            stc.save(op.join(outdir, subject, "mne_src", "src-epo"), overwrite=True)
        else:
            stc.save(op.join(outdir, subject, "mne_src", "src-raw"), overwrite=True)
    return stc
    
    
def calc_noise_cov(data, data_cov_rank, chantypes):
    """Calculate noise covariance.
    
    Parameters
    ----------
    raw : mne.io.Raw
        Raw object.
    data_cov_rank : int
        Rank of the data covariance matrix.
    chantypes : list
        List of channel types to include.
    """
    # In MNE, the noise cov is normally obtained from empty room noise
    # recordings or from a baseline period. Here (if no noise cov is passed in)
    # we mimic what the osl_normalise_sensor_data.m function in Matlab OSL does,
    # by computing a diagonal noise cov with the variances set to the mean
    # variance of each sensor type (e.g. mag, grad, eeg.)
    log_or_print("*** Calculating noise covariance ***")
    
    data = data.pick(chantypes)
    if isinstance(data, mne.io.Raw):
        data_cov = mne.compute_raw_covariance(data, method="empirical", rank=data_cov_rank)
    else:
        data_cov = mne.compute_covariance(data, method="empirical", rank=data_cov_rank)
    
    n_channels = data_cov.data.shape[0]
    noise_cov_diag = np.zeros(n_channels)
    
    for type in chantypes:
        # Indices of this channel type
        type_raw = data.copy().pick(type, exclude="bads")
        inds = []
        for chan in type_raw.info["ch_names"]:
            inds.append(data_cov.ch_names.index(chan))

        # Mean variance of channels of this type
        variance = np.mean(np.diag(data_cov.data)[inds])
        noise_cov_diag[inds] = variance
        log_or_print(f"variance for chantype {type} is {variance}")

    bads = [b for b in data.info["bads"] if b in data_cov.ch_names]
    noise_cov = mne.Covariance(
        noise_cov_diag, data_cov.ch_names, bads, data.info["projs"], nfree=data.n_times
    )
    return noise_cov


def morph_surface(subjects_dir, subject, src_from, subject_to="fsaverage", src_to=None, spacing=None):
    """Morph source space to another subject's surface.
    
    Parameters
    ----------
    subject : str
        Subject ID.
    subjects_dir : str
        Subjects directory.
    src_from : mne.SourceSpaces
        Original source space.
    src_to : str, mne.SourceSpaces
        Destination source space. can be 'fsaverage' or a source space.
    """
    
    # get source spacing from src_to
    
    if subject_to == "fsaverage" and not op.exists(freesurfer_utils.get_coreg_filenames(subjects_dir, "fsaverage")['source_space']):
        # estimate source spacing from src_from
        if 'spacing' not in src_from.info['command_line']:
            src_to = freesurfer_utils.make_fsaverage_src(subjects_dir) # use default
        else:
            spacing = int(src_from.info['command_line'].split('spacing=')[1].split(', ')[0])
            src_to = freesurfer_utils.make_fsaverage_src(subjects_dir, spacing)
        
    morph = mne.compute_source_morph(
        src_from,
        subject_from=subject,
        subject_to=subject_to,
        src_to=src_to,
        subjects_dir=subjects_dir,

    )
    return morph