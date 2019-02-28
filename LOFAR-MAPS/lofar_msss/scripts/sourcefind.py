#!/usr/bin/env python

"""sourcefind.py: Run source-finding on the moment images."""

import sys

import bdsf

__author__ = "Jamie Farnes"
__email__ = "jamie.farnes@oerc.ox.ac.uk"

    
# Run Source-Finding
# ------------------------------------------------------
"""
Currently a hacky source-finding bolt-on and work in progress...
"""
# Change the Python path for Python2:
sys.path.insert(0,'/usr/lib/python2.7/dist-packages/')

filename = '/data/outputs/MOMENTS/Std-P.fits' #  '/data/outputs/imaging_clean_WStack-1.fits'

img = bdsf.process_image(filename, rms_box=(100,100), output_all = True, frequency= 1.4e9, beam=(60.0, 60.0, 0.0))

print("HERE")

img.show_fit()
img.write_catalog(format='fits', catalog_type='srl')

img.export_image(img_type='gaus_model', outfile=filename+'.model')
