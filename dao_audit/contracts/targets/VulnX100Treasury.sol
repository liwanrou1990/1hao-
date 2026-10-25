// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @title VulnX100Treasury  (RESEARCH TARGET — intentionally vulnerable)
/// @notice Reconstruction of the X100 DAO treasury for authorized local-chain
///         auditing. DO NOT DEPLOY.
///
///   [VULN-4] Over-privileged treasury — the governor can make ARBITRARY calls
///            (any target, any calldata, any value) with no allowlist and no
///            per-proposal / per-epoch spending cap. One passed proposal can
///            atomically drain 100% of assets.
contract VulnX100Treasury {
    address public governor;

    event GovernorSet(address governor);
    event ExecutedCall(address indexed target, uint256 value, bytes data);

    constructor() {
        governor = msg.sender;
    }

    // Set-once-ish, but left loose for the reconstruction.
    function setGovernor(address _governor) external {
        require(governor == msg.sender, "not governor");
        governor = _governor;
        emit GovernorSet(_governor);
    }

    receive() external payable {}

    // =========================================================================
    // [VULN-4] OVER-PRIVILEGED TREASURY
    // Arbitrary external call bridge controlled solely by the governor address.
    // No target allowlist, no value ceiling, no rate limit, no guardian veto.
    // =========================================================================
    function execute(address target, uint256 value, bytes calldata data)
        external
        returns (bytes memory)
    {
        require(msg.sender == governor, "not governor"); // only control is "is governor"
        (bool ok, bytes memory ret) = target.call{value: value}(data);
        require(ok, "call failed");
        emit ExecutedCall(target, value, data);
        return ret;
    }
}
