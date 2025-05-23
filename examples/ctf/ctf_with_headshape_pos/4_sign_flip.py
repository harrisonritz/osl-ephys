"""Sign flipping.

Note, this script is only needed if you're training a dynamic network
model (e.g. the HMM) using the time-delay embedded (TDE) approach.

You can skip this if you're training the HMM on amplitude envelope data
or calculating sign-invariant quantities such as amplitude envelope
correlations or power.
"""

# Authors: Chetan Gohil <chetan.gohil@psych.ox.ac.uk>

from osl_ephys import source_recon

# Source directory and subjects to sign flip
outdir = "data"
subjects = [
    "sub-not001_task-resteyesopen",
    "sub-not002_task-resteyesopen",
]

# Find a good template subject to align other subjects to
template = source_recon.find_template_subject(
    outdir, subjects, n_embeddings=15, standardize=True
)

# Settings
config = f"""
    source_recon:
    - fix_sign_ambiguity:
        template: {template}
        n_embeddings: 15
        standardize: True
        n_init: 3
        n_iter: 2500
        max_flips: 20
"""

# Do the sign flipping
source_recon.run_src_batch(config, outdir, subjects)
