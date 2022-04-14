# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 et:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""High-level interface for creating a combi-target on a WebDAV capable server
 """
import logging
import os
from typing import (
    Optional,
    Union,
)
from urllib.parse import urlparse

from datalad.distribution.dataset import (
    Dataset,
    EnsureDataset,
    datasetmethod,
    require_dataset,
)
from datalad.downloaders.credentials import UserPassword
from datalad.interface.base import Interface
from datalad.interface.common_opts import (
    recursion_flag,
    recursion_limit
)
from datalad.interface.utils import eval_results
from datalad.support.param import Parameter
from datalad.support.annexrepo import AnnexRepo
from datalad.support.constraints import (
    EnsureChoice,
    EnsureNone,
    EnsureStr,
)


__docformat__ = "restructuredtext"

lgr = logging.getLogger('datalad_next.create_sibling_webdav')


class CreateSiblingWebDAV(Interface):
    """

    """
    _examples_ = [
    ]

    _params_ = dict(
        url=Parameter(
            args=("url",),
            doc="URL identifying the sibling root on the target WebDAV server",
            constraints=EnsureStr()),
        dataset=Parameter(
            args=("-d", "--dataset"),
            doc="""specify the dataset to process.  If
            no dataset is given, an attempt is made to identify the dataset
            based on the current working directory""",
            constraints=EnsureDataset() | EnsureNone()),
        name=Parameter(
            args=('-s', '--name',),
            metavar='NAME',
            doc="""name of the sibling.
            With `recursive`, the same name will be used to label all
            the subdatasets' siblings.""",
            constraints=EnsureStr() | EnsureNone()),
        storage_name=Parameter(
            args=("--storage-name",),
            metavar="NAME",
            doc="""name of the storage sibling (git-annex special remote).
            Must not be identical to the sibling name. If not specified,
            defaults to the sibling name plus '-storage' suffix. If only
            a storage sibling is created, this setting is ignored, and
            the primary sibling name is used.""",
            constraints=EnsureStr() | EnsureNone()),
        credential=Parameter(
            args=("--credential",),
            doc="""name of the credential providing a user/password credential
            to be used for authorization. The credential can be supplied via
            configuration setting 'datalad.credential.<name>.user|password', or
            environment variable DATALAD_CREDENTIAL_<NAME>_USER|PASSWORD, or will
            be queried from the active credential store using the provided
            name. If none is provided, the last-used token for the
            API URL realm will be used.""",
        ),
        existing=Parameter(
            args=("--existing",),
            constraints=EnsureChoice('skip', 'error', 'reconfigure', None),
            metavar='MODE',
            doc="""action to perform, if a (storage) sibling is already
            configured under the given name and/or a target already exists.
            In this case, a dataset can be skipped ('skip'), an existing target
            repository be forcefully re-initialized, and the sibling
            (re-)configured ('reconfigure'), or the command be instructed to
            fail ('error').""", ),
        recursive=recursion_flag,
        recursion_limit=recursion_limit,
        storage_sibling=Parameter(
            args=("--storage-sibling",),
            dest='storage_sibling',
            metavar='MODE',
            constraints=EnsureChoice(
                'yes', 'export', 'only', 'only-export', 'no'),
            doc="""Both Git history and file content can be hosted on WEBDAV.
            With 'yes', a storage sibling and a Git repository
            sibling are created ('yes').
            Alternatively, creation of the storage sibling can be disabled
            ('no'),
            or a storage sibling can be created only and no Git sibling
            ('only').
            The storage sibling can be set up as a standard git-annex special
            remote that is capable of storage any number of file versions,
            using a content hash based file tree ('yes'|'only'), or
            as an export-type special remote, that can only store a single
            file version corresponding to one unique state of the dataset,
            but using a human-readable data data organization on the WEBDAV
            remote that matches the file tree of the dataset
            ('export'|'only-export')."""),
    )

    @staticmethod
    @datasetmethod(name='create_sibling_webdav')
    @eval_results
    def __call__(
            url: str,
            *,
            dataset: Optional[Union[str, Dataset]] = None,
            name: Optional[str] = None,
            storage_name: Optional[str] = None,
            storage_sibling: Optional[str] = 'yes',
            credential: Optional[str] = None,
            existing: Optional[str] = None,
            recursive: Optional[bool] = False,
            recursion_limit: Optional[int] = None):


        # TODO catch broken URLs
        parsed_url = urlparse(url)
        if not name:
            # not using .netloc to avoid ports to show up in the name
            name = parsed_url.hostname

        if not name:
            # could happen with broken URLs (e.g. without //)
            raise ValueError(
                f"no sibling name given and none could be derived from the URL {url!r}")

        if storage_sibling.startswith('only') and storage_name:
            lgr.warning(
                "Sibling name will be used for storage sibling in "
                "storage-sibling-only mode, but a storage sibling name "
                "was provided"
            )
        if storage_sibling == 'no' and storage_name:
            lgr.warning(
                "Storage sibling setup disabled, but a storage sibling name "
                "was provided"
            )
        if storage_sibling != 'no' and not storage_name:
            storage_name = "{}-storage".format(name)

        if storage_sibling != 'no' and name == storage_name:
            # leads to unresolvable, circular dependency with publish-depends
            raise ValueError("sibling names must not be equal")

        ds = require_dataset(
            dataset or ".",
            check_installed=True,
            purpose=f'create WebDAV sibling(s)')

        res_kwargs = dict(
            action=f'create_sibling_webdav',
            logger=lgr,
            refds=ds.path,
        )

        if not isinstance(ds.repo, AnnexRepo):
            yield {
                **res_kwargs,
                "status": "error",
                "msg": "require annex repo to create WebDAV sibling"
            }
            return

        if parsed_url.query:
            yield {
                **res_kwargs,
                "status": "error",
                "msg": "URLs with query are not yet supported"
            }
            return

        name_base = name or parsed_url.hostname
        git_name = name_base + "-wd-vcs"
        annex_name = name_base + "-wd-tree"

        datalad_annex_url = (
            "datalad_annex::"
            + url
            + "?type=webdav&url={noquery}&encryption=none&exporttree=yes"
            + "" if credential is None else f"&dlacredential={credential}"
        )

        # Add the datalad_annex:: sibling to datalad
        from datalad.api import siblings

        siblings(
            action="add",
            dataset=ds,
            name=git_name,
            url=datalad_annex_url)

        # Add a git-annex webdav special remote. This might requires to set
        # the webdav environment variables accordingly.

        credential_holder = UserPassword(name=credential)()
        os.environ["WEBDAV_USERNAME"] = credential_holder["user"]
        os.environ["WEBDAV_PASSWORD"] = credential_holder["password"]
        ds.repo.call_annex([
            "initremote",
            annex_name,
            "type=webdav",
            f"url={url}",
            "exporttree=yes",
            "encryption=none"
        ])

        yield {
            **res_kwargs,
            "status": "ok"
        }
