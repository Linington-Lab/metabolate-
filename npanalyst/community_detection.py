from collections import defaultdict, namedtuple
from typing import List, Dict
import pandas as pd

import networkx as nx
import community as louvain_modularity

from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import pdist
from sklearn.preprocessing import StandardScaler

from npanalyst.logging import get_logger

logger = get_logger()


Community = namedtuple("Community", ["graph", "table", "assay"])


def add_community_as_node_attribute(
    graph: nx.Graph, community_list: List[Dict], community_key: str = "community"
) -> None:
    """
    This function adds the attribute 'community' to each node of the network.
    It uses the output of the cdlib community detection function.

    Modifies the input graph object by adding the community ID.

    :param graph: networkx graph object. This is the pruned graph that contains only nodes
                  with an activity and cluster score greater or equal to the set threshold.
    :param community_list: louvain function output.
    :param community_key: String / Key that indicates the community of a node.
    """
    community_dict = {}
    for n, i in enumerate(community_list):
        for name in i:
            community_dict[name] = n
    nx.set_node_attributes(graph, community_dict, community_key)
    logger.debug("Community assignment has been added to the graph meta data.")


def community_assignment_df(
    graph: nx.Graph, community_key: str = "community", type_key: str = "type_"
) -> pd.DataFrame:
    """
    This function returns a dataframe that indicates to which community each node
    belongs and what type each node is. A node might be a basket (MS feature) or a sample.

    :param graph: networkx graph object. This is the pruned graph that contains only nodes
                  with an activity and cluster score greater or equal to the set threshold.
    :param community_key: String / Key that indicates the community of a node.
    :param type_key: String / Key that indicates the type of a node (basket or sample).
    :return: Dataframe with node, community and type information.
    """
    community_df = pd.DataFrame(
        [
            {"node": n, "community": nd[community_key], "type": nd[type_key]}
            for n, nd in graph.nodes(data=True)
        ]
    )

    return community_df


def conserve_communities(
    assay_df: pd.DataFrame,
    community_df: pd.DataFrame,
    basket_df: pd.DataFrame,
    graph: nx.Graph,
) -> List[Community]:
    """
    This function conserves a tuple per community that contains a subgraph, a table with a subset of the baskets
    (features) for the web scatter plot and a subset of the original activity dataframe.
    If a community contains 3 or more samples, the order of the shown samples is determined by a optimal-order
    hierarchical clustering technique, using the correlation distance as the metric and the complete linkage method.
    This shall allow easier readability of the heat map. Input data is be standardized (mean=0, sd=1)
    before clustering, indicated by the parameter standardization.


    :param assay_df: Original bioassay readout Pandas dataframe
    :param community_df: Community dataframe generated by the community_assignment_df function.
    :param basket_df: The dataframe that contains all baskets (features)
    :param graph: The graph that contains baskets and samples, filtered by an activity and cluster score
                  threshold.

    Create output data for each community returning a list of Community named tuples
    """
    community_count = max(community_df["community"])
    communities = []
    for community in range(community_count + 1):
        # Retrieve only the samples, not the basket names, from the community_df
        samples = (
            community_df["node"]
            .loc[
                (community_df["community"] == community)
                & (community_df["type"] == "sample")
            ]
            .tolist()
        )
        assay_df_subset = assay_df.loc[samples, :]

        # Create a correlation matrix to sort the dataframe by bioassay relatedness
        if assay_df_subset.shape[0] >= 3:
            try:
                assay_df_subset = optimal_assay_order(assay_df_subset)
            except ValueError:
                logger.warn(
                    f"Unable to optimize assay order using hierarchical clustering on community {community}"
                )
                print(assay_df_subset)

        # Save the subgraph that only contains nodes from the respective community
        nodes = community_df["node"][community_df["community"] == community].tolist()
        subgraph = nx.subgraph(graph, nodes)

        # create a table for scatterplot
        basketids = [
            nid
            for nid, ndata in subgraph.nodes(data=True)
            if ndata["type_"] == "basket"
        ]
        output_table = basket_df[basket_df["BasketID"].isin(map(int, basketids))]
        communities.append(
            Community(graph=subgraph, table=output_table, assay=assay_df_subset)
        )

    return communities


def optimal_assay_order(assay_df_subset: pd.DataFrame) -> pd.DataFrame:
    # Calculate correlation distance between all normalized samples, create a single linkage matrix and
    # order the samples optimal
    X = assay_df_subset.to_numpy(dtype="float64")
    # Facultative standardization of the bioassay readout data
    sc = StandardScaler(with_std=True)
    X_norm = sc.fit_transform(X)
    # Calculation of the distance and the linkage matrix
    distance_matrix = pdist(X_norm, "correlation")
    linkage_matrix = linkage(distance_matrix, method="complete", optimal_ordering=True)
    # Reorder the dataframe by the calculated linkage matrix
    optimized_order = assay_df_subset.index.values[leaves_list(linkage_matrix)]
    return assay_df_subset.reindex(optimized_order)


def louvain(g, weight="weight", resolution=1.0, random_state=None):
    """
    Implementation copied from `cdlib` - https://github.com/GiulioRossetti/cdlib
    to ease installation and remove unnecessary (problematic) dependencies.

    Louvain maximizes a modularity score for each community.
    The algorithm optimises the modularity in two elementary phases:
    (1) local moving of nodes;
    (2) aggregation of the network.
    In the local moving phase, individual nodes are moved to the community that yields the largest increase in the quality function.
    In the aggregation phase, an aggregate network is created based on the partition obtained in the local moving phase.
    Each community in this partition becomes a node in the aggregate network. The two phases are repeated until the quality function cannot be increased further.

    :param g: a networkx object
    :param weight: str, optional the key in graph to use as weight. Default to 'weight'
    :param resolution: double, optional  Will change the size of the communities, default to 1.
    :param random_state:  int, optional  Will set random seed
    :return: list of communities, sorted by size  largest to smalled

    # >>> coms = louvain(G, weight='weight', resolution=1., random_state=None)

    :References:

    Blondel, Vincent D., et al. `Fast unfolding of communities in large networks. <https://iopscience.iop.org/article/10.1088/1742-5468/2008/10/P10008/meta/>`_ Journal of statistical mechanics: theory and experiment 2008.10 (2008): P10008.

    .. note:: Reference implementation: https://github.com/taynaud/python-louvain
    """

    coms = louvain_modularity.best_partition(
        g, weight=weight, resolution=resolution, random_state=random_state
    )

    # Reshaping the results
    coms_to_node = defaultdict(list)
    for n, c in coms.items():
        coms_to_node[c].append(n)

    coms_louvain = sorted(
        (list(c) for c in coms_to_node.values()), key=len, reverse=True
    )
    return list(coms_louvain)


def assign_basket_table(
    basket_df: pd.DataFrame, community_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Takes the basket table (basket_df) and the community table (community_df),
    """
    # filter for basketted features and make then int64 for merging
    cdf_baskets = community_df[community_df["type"] == "basket"].copy()
    cdf_baskets["node"] = cdf_baskets.node.astype("int64")
    # outer joing data to add communites to basket DF deleting node column after
    df1 = pd.merge(
        basket_df,
        cdf_baskets[["node", "community"]],
        left_on="BasketID",
        right_on="node",
        how="outer",
    )
    del df1["node"]
    return df1
