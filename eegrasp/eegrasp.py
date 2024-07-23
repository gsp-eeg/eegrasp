"""
EEGRasP
"""

import numpy as np
from pygsp2 import graphs, learning, graph_learning
from tqdm import tqdm  # TODO: Does it belong here?
from scipy import spatial
import matplotlib.pyplot as plt
import mne
from mne.channels.layout import _auto_topomap_coords
from .plotting import PlottingDefaults, _separate_kwargs


class EEGrasp():
    """
    Class containing functionality to analyze EEG signals.

    Parameters
    ----------
    data
    eeg_pos
    ch_names

    Notes
    -----
    Gaussian Kernel functionallity overlapping with PyGSP2 toolbox. This has
    been purposefully added.
    """

    def __init__(self, data=None, coordinates=None, labels=None):
        """
        Parameters
        ----------
        data: 2-d array, where the first dim are channels and the second is
        samples.
        Coordenates: ndim array with position of the electrodes.
        labels: 1-d array with the channel names.
        """

        self.data = data
        self.coordinates = coordinates
        self.labels = labels
        self.distances = None
        self.graph_weights = None
        self.graph = None

    def euc_dist(self, pos):
        """
        Compute the euclidean distance based on a given set of possitions.

        Parameters
        ----------
        pos -> 2d or 3d array of channels by dimensions

        Returns
        -------
        output: 2d array of channels by channels with the euclidean distance.
        description: compute the euclidean distance between every channel in
        the array
        """

        distance = np.zeros([pos.shape[0], pos.shape[0]],
                            dtype=np.float64)  # Alocate variable
        pos = pos.astype(float)
        for dim in range(pos.shape[1]):
            # Compute the component corresponding to each dimension. Add it to the array
            distance += np.power(pos[:, dim][:, None]-pos[:, dim][None, :], 2)
        distance = np.sqrt(distance)

        return distance

    def gaussian_kernel(self, x, sigma=0.1):
        """
        Gaussian Kernel Weighting function.

        Notes
        -----
        This function is supposed to be used in the PyGSP2 module but
        is repeated here since there is an error in the available version
        of the toolbox (03/04/2024 dd/mm/yyyy)

        References
        ----------
        # D. I. Shuman, S. K. Narang, P. Frossard, A. Ortega and
        # P. Vandergheynst, "The emerging field of signal processing on graphs:
        # Extending high-dimensional data analysis to networks and other
        # irregular domains," in IEEE Signal Processing Magazine, vol. 30,
        # no. 3, pp. 83-98, May 2013, doi: 10.1109/MSP.2012.2235192.
        """
        return np.exp(-np.power(x, 2.) / (2.*np.power(float(sigma), 2)))

    def compute_distance(self, coordinates=None, method='Euclidean', normalize=True):
        """
        Method for computing the distance.

        Returns
        -------
        Distances to be used for the graph computation.
        """

        # If passed, used the coordinates argument
        if isinstance(coordinates, type(None)):
            coordinates = self.coordinates.copy()

        if method == 'Euclidean':
            distances = self.euc_dist(coordinates)
            np.fill_diagonal(distances, 0)

        if normalize:
            # Normalize distances
            distances = distances - np.amin(distances)
            distances = distances / np.amax(distances)

        self.distances = distances

        return distances

    def compute_graph(self, W=None, epsilon=.5, sigma=.1):
        """
        Parameters
        ----------
        W -> if W is passed, then the graph is computed.
        Otherwise the graph will be computed with self.W.
        W should correspond to a non-sparse 2-D array.
        Epsilon -> maximum distance to threshold the array.
        sigma -> Sigma parameter for the gaussian kernel.

        method: NN -> Nearest Neighbor
                Gaussian -> Gaussian Kernel used based on the self.W matrix

        Returns
        -------
        G: Graph structure from PyGSP2
        """

        # If passed, used the W matrix
        if W is None:
            distances = self.distances
            # Check that there is a weight matrix is not a None
            if distances is None:
                raise TypeError(
                    'No distances found. Distances have to be computed if W is not provided')
            graph_weights = self.gaussian_kernel(distances, sigma=sigma)
            graph_weights[distances > epsilon] = 0
            np.fill_diagonal(graph_weights, 0)
            graph = graphs.Graph(graph_weights)
        else:
            graph_weights = W
            graph = graphs.Graph(W)

        if self.coordinates is not None:
            graph.set_coordinates(self.coordinates)

        self.graph = graph
        self.graph_weights = graph_weights

        return graph

    def interpolate_channel(self, graph=None, data=None, missing_idx=None):
        """
        Interpolate missing channel.
        """

        # Check if values are passed or use the instance's
        if isinstance(data, type(None)):
            data = self.data.copy()
        if isinstance(graph, type(None)):
            graph = self.graph

        elif isinstance(missing_idx, type(None)):
            raise TypeError('Parameter missing_idx not specified.')

        time = np.arange(data.shape[1])  # create time array
        mask = np.ones(data.shape[0], dtype=bool)  # Maksing array
        mask[missing_idx] = False

        # Allocate new data array
        reconstructed = np.zeros(data.shape)
        # Iterate over each timepoint
        for t in time:
            reconstructed[:, t] = learning.regression_tikhonov(graph, data[:, t],
                                                               mask, tau=0)
        return reconstructed

    def _return_results(self, error, signal, vparameter, param_name):
        """Function to wrap results into a dictionary.

        Parameters
        ----------
        error ndarray with the errors corresponding to each tried parameter.
        vparameter: ndarray, values of the parameter used in the fit function.
        signal: ndarray, reconstructed signal.

        Notes
        -----
        In order to keep everyting under the same structure this function should be used
        to return the results of any self.fit_* function.
        """
        best_idx = np.argmin(np.abs(error))
        best_param = vparameter[best_idx]

        results = {'error': error,
                   'signal': signal,
                   f'best_{param_name}': best_param,
                   f'{param_name}': vparameter}

        return results

    def _vectorize_matrix(self, mat):
        """
        Vectorize a simetric matrix using the lower triangle.

        Returns
        -------
        vec: ndarray of the lower triangle of mat
        """
        tril_indices = np.tril_indices(len(mat), -1)
        vec = mat[tril_indices]

        return vec

    def fit_epsilon(self, data=None, distances=None, sigma=0.1,
                    missing_idx=None):
        """
        Find the best distance to use as threshold.

        Parameters
        ----------
        distances -> Unthresholded distance matrix (2-dimensional array).
        It can be passed to the instance of
        the class or as an argument of the method.
        sigma -> parameter of the Gaussian Kernel transformation

        Notes
        -----
        It will itterate through all the unique values of the distance matrix.
        data -> 2-dimensional array. The first dim. is Channels
        and second time. It can be passed to the instance class or the method
        """
        # Check if values are passed or use the instance's
        if isinstance(distances, type(None)):
            distances = self.distances.copy()
        if isinstance(data, type(None)):
            data = self.data.copy()

        if isinstance(distances, type(None)) or isinstance(data, type(None)):
            raise TypeError('Check data or W arguments.')
        if isinstance(missing_idx, type(None)):
            raise TypeError('Parameter missing_idx not specified.')

        # Vectorize the distance matrix
        dist_tril = self._vectorize_matrix(distances)

        # Sort and extract unique values
        vdistances = np.sort(np.unique(dist_tril))

        # Create time array
        time = np.arange(data.shape[1])

        # Mask to ignore missing channel
        ch_mask = np.ones(data.shape[0]).astype(bool)
        ch_mask[missing_idx] = False

        # Simulate eliminating the missing channel
        signal = data.copy()
        signal[missing_idx, :] = np.nan

        # Allocate array to reconstruct the signal
        all_reconstructed = np.zeros([len(vdistances), len(time)])

        # Allocate Error array
        error = np.zeros([len(vdistances)])

        # Loop to look for the best parameter
        for i, epsilon in enumerate(tqdm(vdistances)):

            # Compute thresholded weight matrix
            graph = self.compute_graph(distances, epsilon=epsilon, sigma=sigma)

            # Interpolate signal, iterating over time
            reconstructed = self.interpolate_channel(graph, signal,
                                                     missing_idx=missing_idx)
            all_reconstructed[i, :] = reconstructed[missing_idx, :]

            # Calculate error
            error[i] = np.linalg.norm(
                data[missing_idx, :]-all_reconstructed[i, :])

        # Eliminate invalid distances
        valid_idx = ~np.isnan(error)
        error = error[valid_idx]
        vdistances = vdistances[valid_idx]
        all_reconstructed = all_reconstructed[valid_idx, :]

        # Find best reconstruction
        best_idx = np.argmin(np.abs(error))
        best_epsilon = vdistances[np.argmin(np.abs(error))]

        # Save best result in the signal array
        signal[missing_idx, :] = all_reconstructed[best_idx, :]

        # Compute the graph with the best result
        graph = self.compute_graph(distances, epsilon=best_epsilon,
                                   sigma=sigma
                                   )

        results = self._return_results(error, signal, vdistances, 'epsilon')
        return results

    def fit_sigma(self, data=None, distances=None, epsilon=0.5,
                  missing_idx=None, min_sigma=0.1, max_sigma=1, step=0.1):
        """
        Find the best parameter for the gaussian kernel.

        Parameters
        ----------

        Notes
        -----
        Look for the best parameter of sigma for the gaussian kernel. This is
        done by interpolating a channel and comparing the interpolated data to
        the real data. After finding the parameter the graph is saved and
        computed in the instance class. The distance threshold is maintained.
        """

        # Check if values are passed or use the class instance's
        if isinstance(distances, type(None)):
            distances = self.distances.copy()
        if isinstance(data, type(None)):
            data = self.data.copy()

        if isinstance(distances, type(None)) or isinstance(data, type(None)):
            raise TypeError('Check data or W arguments.')
        if isinstance(missing_idx, type(None)):
            raise TypeError('Parameter missing_idx not specified.')

        # Create array of parameter values
        vsigma = np.arange(min_sigma, max_sigma, step=step)

        # Create time array
        time = np.arange(data.shape[1])

        # Mask to ignore missing channel
        ch_mask = np.ones(data.shape[0]).astype(bool)
        ch_mask[missing_idx] = False

        # Simulate eliminating the missing channel
        signal = data.copy()
        signal[missing_idx, :] = np.nan

        # Allocate array to reconstruct the signal
        all_reconstructed = np.zeros([len(vsigma), len(time)])

        # Allocate Error array
        error = np.zeros([len(vsigma)])

        # Loop to look for the best parameter
        for i, sigma in enumerate(tqdm(vsigma)):

            # Compute thresholded weight matrix
            graph = self.compute_graph(distances, epsilon=epsilon, sigma=sigma)

            # Interpolate signal, iterating over time
            reconstructed = self.interpolate_channel(graph, signal,
                                                     missing_idx=missing_idx)
            all_reconstructed[i, :] = reconstructed[missing_idx, :]

            # Calculate error
            error[i] = np.linalg.norm(
                data[missing_idx, :]-all_reconstructed[i, :])

        # Eliminate invalid trials
        valid_idx = ~np.isnan(error)
        error = error[valid_idx]
        vsigma = vsigma[valid_idx]
        all_reconstructed = all_reconstructed[valid_idx, :]

        # Find best reconstruction
        best_idx = np.argmin(np.abs(error))
        best_sigma = vsigma[np.argmin(np.abs(error))]

        # Save best result in the signal array
        signal[missing_idx, :] = all_reconstructed[best_idx, :]

        # Compute the graph with the best result
        graph = self.compute_graph(distances, epsilon=epsilon,
                                   sigma=best_sigma
                                   )

        self.graph = graph

        results = self._return_results(error, signal, vsigma, 'sigma')

        return results

    def learn_graph(self, Z=None, a=0.1, b=0.1,
                    gamma=0.04, maxiter=1000, w_max=np.inf,
                    mode='Average'):
        """Learn the graph based on smooth signals.

        Parameters
        ----------
        Z: ndarra. Distance between the nodes. If not passed,
        the function will try to compute the euclidean distance
        between the data. If self.data is a 2d array it will compute the
        euclidean distance between the channels. If the data is a 3d array
        it will compute the average distance using the 2nd and 3rd dimensions,
        averaging over the 1st one.

        mode: string. Options are: 'Average', 'Trials'. If average,
        the function returns a single W and Z. If 'Trials' the function returns
        a generator list of Ws and Zs.

        Returns
        -------

        W: ndarray. Weighted adjacency matrix or matrices depending on
        mode parameter used. If run in 'Trials' mode then Z is a
        3d array where the first dim corresponds to trials.
        Z: ndarray. Used distance matrix or matrices depending on
        mode parameter used. If run in 'Trials' mode then Z is a
        3d array where the first dim corresponds to trials.

        """

        # If no distance matrix is given compute based on
        # data's euclidean distance
        if Z is None:
            data = self.data.copy()

        # Check if data contains trials
        if data.ndim == 3:

            Zs = np.zeros((data.shape[0], data.shape[1], data.shape[1]))

            # Check if we want to return average or trials
            if mode == 'Trials':

                Ws = np.zeros(
                    (data.shape[0], data.shape[1], data.shape[1]))
                for i, d in enumerate(tqdm(data)):
                    # Compute euclidean distance
                    Z = self.euc_dist(d)

                    W = graph_learning.graph_log_degree(
                        Z, a, b, gamma=gamma, w_max=w_max, maxiter=maxiter)
                    W[W < 1e-5] = 0

                    Ws[i, :, :] = W.copy()
                    Zs[i, :, :] = Z.copy()

                return Ws, Zs

            elif mode == 'Average':

                for i, d in enumerate(tqdm(data)):
                    # Compute euclidean distance
                    Zs[i, :, :] = self.euc_dist(d)

                Z = np.mean(Zs, axis=0)
                W = graph_learning.graph_log_degree(
                    Z, a, b, gamma=gamma, w_max=w_max, maxiter=maxiter)
                W[W < 1e-5] = 0

                return W, Z
        else:
            Z = self.euc_dist(d)

            W = graph_learning.graph_log_degree(
                Z, a, b, gamma=gamma, w_max=w_max, maxiter=maxiter)
            W[W < 1e-5] = 0

            return W, Z

    def plot_graph(self, graph=None, signal=None, coordinates=None, labels=None, montage=None, colorbar=True, axis=None,
                   kind='topoplot', show_names=True, **kwargs):
        """
        Plot the graph over the eeg montage.

        Parameters
        ----------
        graph : PyGSP2 Graph object | None.
            If not passed, the instance's graph will be used.
        signal : ndarray | list | None.
            If not passed, the edge_color parameter passed to the `pygsp2.plot` function will be
            passed.
        coordinates : ndarray | None.
            If not passed, the instance's coordinates will be used.
        labels : list | ndarray | None.
            If not passed, the instance's labels will be used.
        montage : str | mne RawBase | mne EpochsBase | mne EvokedBase | None.
            If None, the instance's coordenates will be used to build a custom montage. If a string
            is passed, it will try to build a montage from the standard built-in libraty. If a
            DigiMontage Classis detected it will use the mne montage object.
        colorbar : bool.
            If True, a colorbar will be plotted.
        axis : matplotlib axis object.
            If not passed, a new figure will be created.
        kind : str.
            Kind of plot to use. Options are 'topoplot' and '3d'.
        vertex_size : int.
            Size of the vertex.
        alphan : float.
            Alpha value for the edges.
        %(pygsp2.plot)s
        %(mne.viz.plot_sensors)s

        Returns
        -------
        figure : matplotlib figure.
            Figure object.
        axis : matplotlib axis.
            Axis object.

        Notes
        -----
        Any argument from `mne.viz.plot_sensors` and `pygsp2.plot` can be passed to the function.

        See Also
        --------
        * pygsp2 function `plotting.plot`
        * mne function `mne.viz.plot_sensors`
        """
        # Load default values
        default_values = PlottingDefaults()

        # Add default values for plotting
        kwargs = default_values.load_defaults(kwargs)
        cmap = kwargs['cmap']

        # Separate kwargs for pygsp2 and mne
        pygsp_arg_list = self.graph.plot.__code__.co_varnames
        mne_arg_list = mne.viz.plot_sensors.__code__.co_varnames

        kwargs_pygsp_plot, kwargs = _separate_kwargs(kwargs, pygsp_arg_list)
        kwargs_mne_plot, kwargs = _separate_kwargs(kwargs, mne_arg_list)

        # Raise exemption if kwargs is not empty
        if len(kwargs) > 0:
            raise ValueError(f'Invalid arguments: {kwargs.keys()}')

        # Handle variables if not passed
        if graph is None:
            graph = self.graph

        if coordinates is None:
            coordinates = self.coordinates

        if axis is None:
            fig = plt.figure()
            if kind == 'topoplot':
                axis = fig.add_subplot(111)
            elif kind == '3d':
                axis = fig.add_subplot(111, projection='3d')
        else:
            fig = axis.get_figure()

        if labels is None:
            labels = self.labels

        if montage is None:
            ch_pos = dict(zip(labels, coordinates))
            montage = mne.channels.make_dig_montage(
                ch_pos=ch_pos, coord_frame='head')
        elif isinstance(montage, str):
            try:
                montage = mne.channels.make_standard_montage(montage)
                labels = montage.ch_names
                kwargs_mne_plot['sphere'] = None
            except ValueError:
                print(
                    f'{montage} Montage not found. Creating custom montage based on self.coordenates...')
                self.plot_graph(graph, coordinates, cmap=cmap, axis=axis,
                                montage=None)
        else:
            kwargs_mne_plot['sphere'] = None

        if signal is None:

            # Plot node size depending on weighted degree
            degree = np.array(graph.dw, dtype=float)
            degree /= np.max(degree)
            kwargs_pygsp_plot['vertex_size'] = degree

            # Plot edge color depending on the edge weights
            edge_weights = self.graph.get_edge_list()[2]
            # edge_weights_norm = edge_weights - np.min(edge_weights)
            edge_weights_norm = edge_weights / np.max(edge_weights)
            kwargs_pygsp_plot['edge_color'] = plt.cm.get_cmap(
                cmap)(edge_weights_norm)
            cbar = fig.colorbar(plt.cm.ScalarMappable(cmap=cmap),
                                ax=axis, label='Edge Weights')
            cbar.set_ticks([0, 0.5, 1])
            cbar.ax.set_yticklabels(
                np.round([0, np.max(edge_weights)/2, np.max(edge_weights)], 2))

        elif isinstance(signal, (list, np.ndarray)):

            signal = np.array(signal, dtype=float)
            signal -= np.min(signal)
            signal /= np.max(signal)
            kwargs_pygsp_plot['edge_color'] = plt.cm.get_cmap(cmap)(signal)

        # Plot the montage
        if kind == 'topoplot':

            info = mne.create_info(labels, sfreq=250, ch_types="eeg")
            info.set_montage(montage)

            xy = _auto_topomap_coords(
                info, None, True, to_sphere=True, sphere=kwargs_mne_plot['sphere'])
            graph.set_coordinates(xy)
            figure = mne.viz.plot_sensors(info, kind='topomap', show_names=show_names, ch_type='eeg',
                                          axes=axis, show=False, **kwargs_mne_plot)
            figure, axis = graph.plot(ax=axis, colorbar=colorbar,
                                      **kwargs_pygsp_plot)

        elif kind == '3d':

            info = mne.create_info(labels, sfreq=250, ch_types="eeg")
            info.set_montage(montage)
            eeg_pos = info.get_montage().get_positions()['ch_pos']
            eeg_pos = np.array([pos for _, pos in eeg_pos.items()])

            dev_head_t = info["dev_head_t"]
            eeg_pos = mne.transforms.apply_trans(dev_head_t, eeg_pos)
            graph.set_coordinates(eeg_pos)

            figure = mne.viz.plot_sensors(
                info, kind='3d', show_names=True, axes=axis, show=False, **kwargs_mne_plot)
            figure, axis = graph.plot(
                ax=axis, colorbar=colorbar, **kwargs_pygsp_plot)

        return (figure, axis)
