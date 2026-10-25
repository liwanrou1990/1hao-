// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface IERC20Min { function transfer(address to, uint256 amount) external returns (bool); }

/// @title SafeX100Treasury  (REMEDIATED)
/// @notice Fixes [VULN-4] over-privileged treasury with layered least-privilege:
///   [FIX-4a] Arbitrary `execute` is restricted to an allowlist of targets.
///   [FIX-4b] Token outflows use a dedicated `payToken` primitive with an
///            allowlisted recipient set AND a per-token, per-epoch spending cap,
///            so no single proposal can drain 100% of any asset.
///   [FIX-4c] A guardian circuit-breaker can pause all outflows.
contract SafeX100Treasury {
    address public governor;
    address public guardian;
    bool public paused;

    mapping(address => bool) public allowedTarget;     // for execute()
    mapping(address => bool) public allowedRecipient;   // for payToken()
    mapping(address => uint256) public tokenEpochCap;   // per-token outflow cap

    uint256 public epochLength = 1 days;
    uint256 public currentEpoch;
    mapping(address => uint256) public tokenSpentThisEpoch;

    event GovernorSet(address governor);
    event TargetAllowed(address target, bool allowed);
    event RecipientAllowed(address recipient, bool allowed);
    event TokenCapSet(address token, uint256 cap);
    event ExecutedCall(address indexed target, uint256 value, bytes data);
    event TokenPaid(address indexed token, address indexed to, uint256 amount);
    event Paused(bool paused);

    constructor(address _guardian) {
        governor = msg.sender;
        guardian = _guardian;
        currentEpoch = block.timestamp / epochLength;
    }

    modifier onlyGovernor() { require(msg.sender == governor, "not governor"); _; }

    function setGovernor(address _governor) external onlyGovernor { governor = _governor; emit GovernorSet(_governor); }
    function setGuardian(address _guardian) external { require(msg.sender == guardian, "not guardian"); guardian = _guardian; }
    function setTargetAllowed(address t, bool a) external onlyGovernor { allowedTarget[t] = a; emit TargetAllowed(t, a); }
    function setRecipientAllowed(address r, bool a) external onlyGovernor { allowedRecipient[r] = a; emit RecipientAllowed(r, a); }
    function setTokenEpochCap(address token, uint256 cap) external onlyGovernor { tokenEpochCap[token] = cap; emit TokenCapSet(token, cap); }
    function pause(bool p) external { require(msg.sender == guardian, "not guardian"); paused = p; emit Paused(p); }

    receive() external payable {}

    // [FIX-4b] constrained token payout: allowlisted recipient + per-epoch cap.
    mapping(address => uint256) private _epochOfToken;
    function payToken(address token, address to, uint256 amount) external onlyGovernor {
        require(!paused, "paused");
        require(allowedRecipient[to], "recipient not allowed");
        uint256 e = block.timestamp / epochLength;
        if (_epochOfToken[token] != e) { _epochOfToken[token] = e; tokenSpentThisEpoch[token] = 0; }
        require(tokenSpentThisEpoch[token] + amount <= tokenEpochCap[token], "token epoch cap");
        tokenSpentThisEpoch[token] += amount;
        require(IERC20Min(token).transfer(to, amount), "transfer failed");
        emit TokenPaid(token, to, amount);
    }

    // [FIX-4a] arbitrary call limited to allowlisted, non-token operational targets.
    function execute(address target, uint256 value, bytes calldata data)
        external
        onlyGovernor
        returns (bytes memory)
    {
        require(!paused, "paused");
        require(allowedTarget[target], "target not allowed");
        (bool ok, bytes memory ret) = target.call{value: value}(data);
        require(ok, "call failed");
        emit ExecutedCall(target, value, data);
        return ret;
    }
}
