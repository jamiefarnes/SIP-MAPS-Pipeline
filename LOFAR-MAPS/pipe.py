#!/usr/bin/env python

import os
import time as t
import subprocess
import argparse

import numpy as np

import pyrap.tables as tab

from processing_components.image.operations import import_image_from_fits, export_image_to_fits, qa_image
from processing_components.visibility.operations import append_visibility

from lofar_msss.metamorphosis.filter import uv_cut, uv_advice
from lofar_msss.metamorphosis.iono import get_ion_rotation_measures_maps
from lofar_msss.telescopetools.initinst import init_inst
from lofar_msss.accretion.ms import load
from lofar_msss.pipelines.maps import maps_imaging, arl_data_future

import dask
import dask.array as da
from dask.distributed import Client
from distributed.diagnostics import progress
from distributed import wait

__author__ = "Jamie Farnes"
__email__ = "jamie.farnes@oerc.ox.ac.uk"


def main(args):
    """
    Initialising launch sequence.
    """
    # ------------------------------------------------------
    # Print some stuff to show that the code is running:
    print("")
    os.system("printf 'Running the \033[5mLOFAR MSSS/MAPS\033[m pipeline\n'")
    print("")
    # Set the directory for the moment images:
    MOMENTS_DIR = args.outputs + '/MOMENTS'
    # Check that the output directories exist, if not then create:
    os.makedirs(args.outputs, exist_ok=True)
    os.makedirs(MOMENTS_DIR, exist_ok=True)
    # Set the polarisation definition of the instrument:
    POLDEF = init_inst(args.inst)
    
    # Setup Variables for SIP services
    # ------------------------------------------------------
    # Define the Queue Producer settings:
    if args.queues:
        queue_settings = {'bootstrap.servers': 'scheduler:9092', 'message.max.bytes': 100000000}  #10.60.253.31:9092
    
    # Setup the Confluent Kafka Queue
    # ------------------------------------------------------
    if args.queues:
        from confluent_kafka import Producer
        import pickle
        # Create an SDP queue:
        sip_queue = Producer(queue_settings)
    
    # Define a Data Array Format
    # ------------------------------------------------------
    def gen_data(channel):
        return np.array([vis1[channel], vis2[channel], channel, stations, lofar_stat_pos, args.iono, False, args.plots, float(args.uvcut), float(args.pixels), POLDEF, args.outputs, float(args.angres), ionRM1, times1, time_indices1, ionRM2, times2, time_indices2, args.twod, npixel_advice, cell_advice])

    # Setup the Dask Cluster
    # ------------------------------------------------------
    starttime = t.time()

    dask.config.set(get=dask.distributed.Client.get)
    client = Client(args.daskaddress)  # scheduler for Docker container, localhost for P3.
    
    print("Dask Client details:")
    print(client)
    print("")

    # Define channel range for 1 subband, each containing 40 channels:
    channel_range = np.array(range(int(args.channels)))

    # Load the data into memory:
    """
    The input data should be interfaced with Buffer Management.
    """
    print("Loading data:")
    print("")
    vis1 = [load('%s/%s' % (args.inputs, args.ms1), range(channel, channel+1), POLDEF) for channel in range(0, int(args.channels))]
    vis2 = [load('%s/%s' % (args.inputs, args.ms2), range(channel, channel+1), POLDEF) for channel in range(0, int(args.channels))]

    # Obtain Ionospheric Faraday rotation estimates
    # ------------------------------------------------------
    """
    The Measurement Set defines the station and dipole positions, the phase centre, and the channel frequencies (and reference frequency) for which the LOFAR beam will be evaluated.
    """
    myms = tab.table(args.inputs + '/' + args.ms1)
    stations = tab.table(myms.getkeyword('ANTENNA')).getcol('NAME')
    lofar_stat_pos = tab.table(myms.getkeyword('ANTENNA')).getcol('POSITION')  # in ITRF metres

    if args.iono:
        print("Obtaining Ionospheric TEC data:")
        print("")
        ionRM1, times1, time_indices1 = get_ion_rotation_measures_maps(vis1[0], stations, lofar_stat_pos)
        ionRM2, times2, time_indices2 = get_ion_rotation_measures_maps(vis2[0], stations, lofar_stat_pos)
        # Save the median Faraday rotation to disk, as an instrumental estimate:
        np.savetxt('%s/ionFR.txt' % (args.outputs), [np.median(np.concatenate((ionRM1, ionRM2), axis=0))], fmt="%s")
    else:
        ionRM1 = None
        times1 = None
        time_indices1 = None
        ionRM2 = None
        times2 = None
        time_indices2 = None

    # Prepare Measurement Set
    # ------------------------------------------------------
    # Combine MSSS snapshots:
    vis_advice = append_visibility(vis1[0], vis2[0])
    
    # Apply a uv-distance cut to the data:
    vis_advice = uv_cut(vis_advice, float(args.uvcut))
    npixel_advice, cell_advice = uv_advice(vis_advice, float(args.uvcut), float(args.pixels))
    
    # Begin imaging via the Dask cluster
    # ------------------------------------------------------
    # Submit data for each channel to the client, and return an image:

    # Scatter all the data in advance to all the workers:
    """
    The data here could be passed via Data Queues.
    Queues may not be ideal. Data throughput challenges.
    Need to think more about the optimum approach.
    """
    print("Scatter data to workers:")
    print("")
    big_job = [client.scatter(gen_data(channel)) for channel in channel_range]

    # Submit jobs to the cluster and create a list of futures:
    futures = [client.submit(maps_imaging, big_job[channel], pure=False, retries=3) for channel in channel_range]
    """
    The dprepb_imaging function could generate QA, logging, and pass this information via Data Queues.
    Queues work well for this.
    Python logging calls are preferable. Send them to a text file on the node.
    Run another service that watches that file. Or just read from standard out.
    The Dockerisation will assist with logs.
    """

    print("Imaging on workers:")
    # Watch progress:
    progress(futures)

    # Wait until all futures are complete:
    wait(futures)
    
    # Check that no futures have errors, if so resubmit:
    for future in futures:
        if future.status == 'error':
            print("ERROR: Future", future, "has 'error' status, as:")
            print(client.recreate_error_locally(future))
            print("Rerunning...")
            print("")
            index = futures.index(future)
            futures[index].cancel()
            futures[index] = client.submit(maps_imaging, big_job[index], pure=False, retries=3)

    # Wait until all futures are complete:
    wait(futures)

    # Gather results from the futures:
    results = client.gather(futures, errors='raise')

    # Run QA on ARL objects and produce to queue:
    if args.queues:
        print("Adding QA to queue:")
        for result in results:
            sip_queue.produce('qa', pickle.dumps(qa_image(result), protocol=2))

        sip_queue.flush()

    # Return the data element of each ARL object, as a Dask future:
    futures = [client.submit(arl_data_future, result, pure=False, retries=3) for result in results]

    progress(futures)

    wait(futures)


    # Calculate the Moment images
    # ------------------------------------------------------
    # Now use 'distributed Dask arrays' in order to parallelise the Moment image calculation:
    # Construct a small Dask array for every future:
    print("")
    print("Calculating Moment images:")
    print("")
    arrays = [da.from_delayed(future, dtype=np.dtype('float64'), shape=(1, 4, 512, 512)) for future in futures]

    # Stack all small Dask arrays into one:
    stack = da.stack(arrays, axis=0)

    # Combine chunks to reduce overhead - is initially (40, 1, 4, 512, 512):
    stack = stack.rechunk((1, 1, 4, 64, 64))

    # Spread the data around on the cluster:
    stack = client.persist(stack)
    # Data is now coordinated by the single logical Dask array, 'stack'.

    # Save the Moment images:
    """
    The output moment images should be interfaced with Buffer Management.
    
    Need to know more about the Buffer specification.
    Related to initial data distribution also/staging.
    """
    print("Saving Moment images to disk:")
    print("")
    # First generate a template:
    image_template = import_image_from_fits('%s/imaging_dirty_WStack-%s.fits' % (args.outputs, 0))

    # Output mean images:
    # I:
    image_template.data = np.expand_dims(stack[:, :, 0, :, :].mean(axis=0).compute(), axis=0)
    print(image_template.wcs.wcs.ctype)
    print(image_template.wcs.wcs.cdelt)
    print(image_template.wcs.wcs.crpix)
    print(image_template.wcs.wcs.cunit)
    print(image_template.wcs.wcs.crval)
    print(image_template.frequency)
    print(image_template.data.shape)
    print(image_template.data)
    print("Expanded dims: was 1,512,512 - should now be 1,1,512,512?))
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Mean-%s.fits' % (MOMENTS_DIR, 'I'))

    # Q:
    image_template.data = stack[:, :, 1, :, :].mean(axis=0).compute()
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Mean-%s.fits' % (MOMENTS_DIR, 'Q'))

    # U:
    image_template.data = stack[:, :, 2, :, :].mean(axis=0).compute()
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Mean-%s.fits' % (MOMENTS_DIR, 'U'))

    # P:
    image_template.data = da.sqrt((da.square(stack[:, :, 1, :, :]) + da.square(stack[:, :, 2, :, :]))).mean(axis=0).compute()
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Mean-%s.fits' % (MOMENTS_DIR, 'P'))

    # Output standard deviation images:
    # I:
    image_template.data = stack[:, :, 0, :, :].std(axis=0).compute()
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Std-%s.fits' % (MOMENTS_DIR, 'I'))

    # Q:
    image_template.data = stack[:, :, 1, :, :].std(axis=0).compute()
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Std-%s.fits' % (MOMENTS_DIR, 'Q'))

    # U:
    image_template.data = stack[:, :, 2, :, :].std(axis=0).compute()
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Std-%s.fits' % (MOMENTS_DIR, 'U'))

    # P:
    image_template.data = da.sqrt((da.square(stack[:, :, 1, :, :]) + da.square(stack[:, :, 2, :, :]))).std(axis=0).compute()
    # Run QA on ARL objects and produce to queue:
    if args.queues:
        sip_queue.produce('qa', pickle.dumps(qa_image(image_template), protocol=2))
    # Export the data to disk:
    export_image_to_fits(image_template, '%s/Std-%s.fits' % (MOMENTS_DIR, 'P'))

    # Flush queue:
    if args.queues:
        sip_queue.flush()

    # Make a tarball of moment images:
    subprocess.call(['tar', '-cvf', '%s/moment.tar' % (MOMENTS_DIR), '%s/' % (MOMENTS_DIR)])
    subprocess.call(['gzip', '-9f', '%s/moment.tar' % (MOMENTS_DIR)])

    endtime = t.time()
    print(endtime-starttime)

    # Begin source-finding:
    os.system('python2 ./SIP-MAPS-Pipeline/LOFAR-MAPS/lofar_msss/scripts/sourcefind.py')


# Define the arguments for the pipeline:
ap = argparse.ArgumentParser()
ap.add_argument('-d', '--daskaddress', help='Address of the Dask scheduler [default scheduler:8786]', default='scheduler:8786')
ap.add_argument('-c', '--channels', help='Number of channels to process [default 40]', default=40)

ap.add_argument('-inp', '--inputs', help='Input data directory [default /data/inputs]', default='/data/inputs')
ap.add_argument('-out', '--outputs', help='Output data directory [default /data/outputs]', default='/data/outputs')
ap.add_argument('-ms1', '--ms1', help='Measurement Set 1 [default sim-1.ms]', default='sim-1.ms')
ap.add_argument('-ms2', '--ms2', help='Measurement Set 2 [default sim-2.ms]', default='sim-2.ms')
ap.add_argument('-q', '--queues', help='Enable Queues? [default False]', default=True)
ap.add_argument('-p', '--plots', help='Output diagnostic plots? [default False]', default=False)
ap.add_argument('-2d', '--twod', help='2D imaging [True] or wstack imaging [False]? [default False]', default=False)
ap.add_argument('-i','--iono',help='Correct for ionospheric Faraday rotation? [default False]',default=False)

ap.add_argument('-uv', '--uvcut', help='Cut-off for the uv-data [default 450]', default=450.0)
ap.add_argument('-a', '--angres', help='Force the angular resolution to be consistent across the band, in arcmin FWHM [default 8.0]', default=8.0)
ap.add_argument('-pix', '--pixels', help='The number of pixels/sampling across the observing beam [default 5.0]', default=5.0)
ap.add_argument('-ins', '--inst', help='Instrument name (for future use) [default LOFAR]', default='LOFAR')
args = ap.parse_args()
main(args)
