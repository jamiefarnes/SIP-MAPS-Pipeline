#!/usr/bin/env python

"""sourcefind.py: Run source-finding on the moment images."""

import sys
# Change the Python path for Python2:
sys.path.insert(0,'/usr/lib/python2.7/dist-packages/')

import bdsf

__author__ = "Jamie Farnes"
__email__ = "jamie.farnes@oerc.ox.ac.uk"

    
# Run Source-Finding
# ------------------------------------------------------
"""
Currently a hacky source-finding bolt-on and work in progress...
"""
filename = '/data/outputs/MOMENTS/Mean-I.fits' #  '/data/outputs/imaging_clean_WStack-1.fits'

img = bdsf.process_image(filename, frequency= 150e6, beam=(8.0/60.0, 8.0/60.0, 0.0), thresh='fdr', thresh_isl=4.0, thresh_pix=6.0, output_all=True)

#img = bdsf.process_image(filename, frequency= 150e6, beam=(8.0/60.0, 8.0/60.0, 0.0), thresh='fdr', thresh_isl=4.0, thresh_pix=6.0, savefits_residim=True, output_all=True)

print("HERE")

img.show_fit()
img.write_catalog(format='fits', catalog_type='srl')

img.export_image(img_type='gaus_model', outfile=filename+'.model')
