from abc import abstractmethod
from typing import Callable, Dict, Iterable, Optional, Tuple, Union

import torch
import torch.nn as nn
from pyro.infer.predictive import Predictive

from ._decorators import auto_move_data
from ._pyro import AutoMoveDataPredictive


class LossRecorder:
    """
    Loss signature for models.

    This class provides an organized way to record the model loss, as well as
    the components of the ELBO. This may also be used in MLE, MAP, EM methods.
    The loss is used for backpropagation during infernce. The other parameters
    are used for logging/early stopping during inference.

    Parameters
    ----------
    loss
        Tensor with loss for minibatch. Should be one dimensional with one value.
        Note that loss should be a :class:`~torch.Tensor` and not the result of `.item()`.
    reconstruction_loss
        Reconstruction loss for each observation in the minibatch.
    kl_local
        KL divergence associated with each observation in the minibatch.
    kl_global
        Global kl divergence term. Should be one dimensional with one value.
    """

    def __init__(
        self,
        loss: torch.Tensor,
        reconstruction_loss: torch.Tensor,
        kl_local: torch.Tensor,
        kl_global: torch.Tensor = torch.Tensor([0]),
    ):
        self._loss = loss if isinstance(loss, dict) else dict(loss=loss)
        self._reconstruction_loss = (
            reconstruction_loss
            if isinstance(reconstruction_loss, dict)
            else dict(reconstruction_loss=reconstruction_loss)
        )
        self._kl_local = (
            kl_local if isinstance(kl_local, dict) else dict(kl_local=kl_local)
        )
        self._kl_global = (
            kl_global if isinstance(kl_global, dict) else dict(kl_global=kl_global)
        )

    @staticmethod
    def _get_dict_sum(dictionary):
        total = 0.0
        for value in dictionary.values():
            total += value
        return total

    @property
    def loss(self) -> torch.Tensor:
        return self._get_dict_sum(self._loss)

    @property
    def reconstruction_loss(self) -> torch.Tensor:
        return self._get_dict_sum(self._reconstruction_loss)

    @property
    def kl_local(self) -> torch.Tensor:
        return self._get_dict_sum(self._kl_local)

    @property
    def kl_global(self) -> torch.Tensor:
        return self._get_dict_sum(self._kl_global)

    @property
    def elbo(self):
        return


class BaseModuleClass(nn.Module):
    def __init__(
        self,
    ):
        super().__init__()

    @auto_move_data
    def forward(
        self,
        tensors,
        get_inference_input_kwargs: Optional[dict] = None,
        get_generative_input_kwargs: Optional[dict] = None,
        inference_kwargs: Optional[dict] = None,
        generative_kwargs: Optional[dict] = None,
        loss_kwargs: Optional[dict] = None,
        compute_loss=True,
    ) -> Union[
        Tuple[torch.Tensor, torch.Tensor],
        Tuple[torch.Tensor, torch.Tensor, LossRecorder],
    ]:
        """
        Forward pass through the network.

        Parameters
        ----------
        tensors
            tensors to pass through
        get_inference_input_kwargs
            Keyword args for `_get_inference_input()`
        get_generative_input_kwargs
            Keyword args for `_get_generative_input()`
        inference_kwargs
            Keyword args for `inference()`
        generative_kwargs
            Keyword args for `generative()`
        loss_kwargs
            Keyword args for `loss()`
        compute_loss
            Whether to compute loss on forward pass. This adds
            another return value.
        """
        inference_kwargs = _get_dict_if_none(inference_kwargs)
        generative_kwargs = _get_dict_if_none(generative_kwargs)
        loss_kwargs = _get_dict_if_none(loss_kwargs)
        get_inference_input_kwargs = _get_dict_if_none(get_inference_input_kwargs)
        get_generative_input_kwargs = _get_dict_if_none(get_generative_input_kwargs)

        inference_inputs = self._get_inference_input(
            tensors, **get_inference_input_kwargs
        )
        inference_outputs = self.inference(**inference_inputs, **inference_kwargs)
        generative_inputs = self._get_generative_input(
            tensors, inference_outputs, **get_generative_input_kwargs
        )
        generative_outputs = self.generative(**generative_inputs, **generative_kwargs)
        if compute_loss:
            losses = self.loss(
                tensors, inference_outputs, generative_outputs, **loss_kwargs
            )
            return inference_outputs, generative_outputs, losses
        else:
            return inference_outputs, generative_outputs

    @abstractmethod
    def _get_inference_input(self, tensors: Dict[str, torch.Tensor], **kwargs):
        """Parse tensors dictionary for inference related values."""

    @abstractmethod
    def _get_generative_input(
        self,
        tensors: Dict[str, torch.Tensor],
        inference_outputs: Dict[str, torch.Tensor],
        **kwargs,
    ):
        """Parse tensors dictionary for inference related values."""

    @abstractmethod
    def inference(
        self,
        *args,
        **kwargs,
    ) -> dict:
        """
        Run the inference (recognition) model.

        In the case of variational inference, this function will perform steps related to
        computing variational distribution parameters. In a VAE, this will involve running
        data through encoder networks.
        """

    @abstractmethod
    def generative(self, *args, **kwargs) -> dict:
        """
        Run the generative model.

        This function should return the parameters associated with the likelihood of the data.
        This is typically written as :math:`p(x|z)`.
        """

    @abstractmethod
    def loss(self, *args, **kwargs) -> LossRecorder:
        """
        Compute the loss for a minibatch of data.

        This function uses the outputs of the inference and generative functions to compute
        a loss. This many optionally include other penalty terms, which should be computed here.
        """

    @abstractmethod
    def sample(self, *args, **kwargs):
        """Generate samples from the learned model."""


def _get_dict_if_none(param):
    param = {} if not isinstance(param, dict) else param

    return param


class PyroBaseModuleClass(nn.Module):
    """
    Base module class for Pyro models.

    In Pyro, `model` and `guide` should have the same signature. Out of convenience,
    the forward function of this class passes through to the forward of the `model`.

    There are two ways this class can be equipped with a model and a guide. First,
    `model` and `guide` can be class attributes that are :class:`~pyro.nn.PyroModule`
    instances. Second, `model` and `guide` methods can be written (see Pyro scANVI example)
    https://pyro.ai/examples/scanvi.html
    """

    def __init__(self):
        super().__init__()

    @staticmethod
    @abstractmethod
    def _get_fn_args_from_batch(
        tensor_dict: Dict[str, torch.Tensor]
    ) -> Union[Iterable, dict]:
        """
        Parse the minibatched data to get the correct inputs for `model` and `guide`.

        In Pyro, `model` and `guide` must have the same signature. This is a helper method
        that gets the args and kwargs for these two methods. This helper method aids `forward` and
        `guide` in having transparent signatures, as well as allows use of our generic
        :class:`~scvi.dataloaders.AnnDataLoader`.

        Returns
        -------
        args and kwargs for the functions, args should be an Iterable and kwargs a dictionary.
        """

    def create_predictive(
        self,
        model: Optional[Callable] = None,
        posterior_samples: Optional[dict] = None,
        guide: Optional[Callable] = None,
        num_samples: Optional[int] = None,
        return_sites: Tuple[str] = (),
        parallel: bool = False,
    ) -> Predictive:
        """
        Creates a :class:`~pyro.infer.Predictive` object.

        Parameters
        ----------
        model
            Python callable containing Pyro primitives. Defaults to `self.model`.
        posterior_samples
            Dictionary of samples from the posterior
        guide
            Optional guide to get posterior samples of sites not present
            in `posterior_samples`. Defaults to `self.guide`
        num_samples
            Number of samples to draw from the predictive distribution.
            This argument has no effect if ``posterior_samples`` is non-empty, in which case,
            the leading dimension size of samples in ``posterior_samples`` is used.
        return_sites
            Sites to return; by default only sample sites not present
            in `posterior_samples` are returned.
        parallel
            predict in parallel by wrapping the existing model
            in an outermost `plate` messenger. Note that this requires that the model has
            all batch dims correctly annotated via :class:`~pyro.plate`. Default is `False`.
        """

        if model is None:
            model = self.model
        if guide is None:
            guide = self.guide
        predictive = AutoMoveDataPredictive(
            model=model,
            posterior_samples=posterior_samples,
            guide=guide,
            num_samples=num_samples,
            return_sites=return_sites,
            parallel=parallel,
        )
        # necessary to comply with auto_move_data decorator
        predictive.eval()

        return predictive

    def forward(self, *args, **kwargs):
        """Passthrough to Pyro model."""
        return self.model(*args, **kwargs)
