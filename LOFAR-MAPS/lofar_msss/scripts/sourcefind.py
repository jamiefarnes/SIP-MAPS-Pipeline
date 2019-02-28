
"""
    Change the Python path for Python2:
"""
import sys
"""
sys.path = ['/usr/lib/python2.7/dist-packages/']
"""
sys.path.insert(0,'/usr/lib/python2.7/dist-packages/')

print sys.path

import bdsf

filename = '/data/outputs/MOMENTS/Std-P.fits'
img = bdsf.process_image(filename, rms_box=(100,100), output_all = True)

img.show_fit()
img.write_catalog(format='fits', catalog_type='srl')

img.export_image(img_type='gaus_model', outfile=filename+'.model')
